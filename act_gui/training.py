#
# This source file is part of the Alexandria Chemistry Toolkit.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
"""!
@file
@brief Training a force field with alexandria train_ff: which parameters
are trained, on how many processes, and what of the result goes to the
browser.

The user chooses the kinds of parameters to train, such as sigma and
epsilon, and the properties to train on, which are those of an evaluation
(module evaluation). More settings of train_ff can be given: the annealing
of the Monte Carlo temperature, within the iterations and over the
generations, the temperature for a Boltzmann weighting of the reference
energies, and the crossover and mutation of the genetic algorithm.
train_ff trains a population of individuals, sets of
parameters, as many as it has processes unless the user gives another
number: with fewer individuals each gets helper processes, with more (only
for the genetic algorithm) each process computes several in turn.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import evaluation


class TrainingError(ValueError):
    """!
    @brief A training that cannot be started. The message is for the user.
    """


## What the name of a kind of parameter looks like, such as sigma or bondlength
_PARAMETER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")

## The largest number of kinds of parameters to train
MAX_PARAMETERS = 50

## The largest part of the compounds that can be kept for testing
MAX_TEST = 0.5

## The largest gene pool that is read to count its individuals
MAX_POOL_BYTES = 64 * 1024 * 1024

## The largest number of individuals
MAX_POPULATION = 1024

## The optimizers of train_ff, and whether they need more than one process and
## an even number of individuals
OPTIMIZERS: Dict[str, bool] = {"MCMC": False, "HYBRID": True, "GA": True}

## The optimizer when none is chosen: the one that runs on one process
DEFAULT_OPTIMIZER = "MCMC"

## The annealing of the Monte Carlo temperature within the iterations: where
## it starts and where the temperature is zero, as fractions of the iterations
ANNEALING = ("anneal_begin", "anneal_end")
## The same over the generations, for the genetic algorithm with Monte Carlo
GLOBAL_ANNEALING = ("anneal_global_begin", "anneal_global_end")
## The settings of the genetic algorithm: the number of crossover points and
## the probabilities of a crossover and of a mutation
GENETIC = ("n_crossovers", "pr_cross", "pr_mut")
## More settings of train_ff that the user can give, by option name. The
## first is the temperature of the Boltzmann weighting of reference energies.
SETTINGS = ("ener_boltz_temp",) + ANNEALING + GLOBAL_ANNEALING + GENETIC
## What the annealing is called in messages
_ANNEALING_NAMES = {ANNEALING: "the annealing", GLOBAL_ANNEALING: "the annealing over the "
                    "generations"}


def options(spec: Any) -> Dict[str, Any]:
    """!
    @brief The options of train_ff for what is trained.
    @param spec The choices: an object with "fit", the names of the kinds
    of parameters to train.
    @return The options.
    @throws TrainingError if there is no name, or one that is not a name.
    """
    if not isinstance(spec, Mapping):
        raise TrainingError("The request does not say what to train")
    fit = spec.get("fit")
    if not isinstance(fit, (list, tuple)) or not fit:
        raise TrainingError("Choose at least one kind of parameter to train")
    if len(fit) > MAX_PARAMETERS:
        raise TrainingError("More than %d kinds of parameters to train" % MAX_PARAMETERS)
    for name in fit:
        if not isinstance(name, str) or not _PARAMETER.fullmatch(name):
            raise TrainingError("'%s' is not the name of a kind of parameter" % (name,))
    if len(set(fit)) != len(fit):
        raise TrainingError("A kind of parameter is given twice")
    return {"fit": " ".join(fit)}


def test_fraction(spec: Any) -> float:
    """!
    @brief The part of the compounds that is kept for testing.
    @param spec The choices: an object that may have "test", a number.
    @return The part, zero if none is given.
    @throws TrainingError if it is not a number between zero and MAX_TEST.
    """
    value = spec.get("test") if isinstance(spec, Mapping) else None
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not 0 <= value <= MAX_TEST:
        raise TrainingError("The part of the compounds for testing is a number "
                            "between 0 and %g" % MAX_TEST)
    return float(value)


def processes(value: Any, optimizer: Any, largest: int) -> int:
    """!
    @brief Check the number of processes of a training.
    @param value     The number that is asked for, None for one.
    @param optimizer The optimizer, None for the default.
    @param largest   The largest number that is allowed.
    @return The number of processes.
    @throws TrainingError if it is not a number within the limits, or too
    small for the optimizer.
    """
    number = 1 if value is None else value
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= largest:
        raise TrainingError("The number of processes is between 1 and %d" % largest)
    name = DEFAULT_OPTIMIZER if optimizer is None else optimizer
    if name not in OPTIMIZERS:
        raise TrainingError("The optimizer is one of %s" % ", ".join(OPTIMIZERS))
    if OPTIMIZERS[name] and number < 2:
        # The master hands out the individuals to the others
        raise TrainingError("The optimizer %s needs two processes at least" % name)
    return number


def population(spec: Any, optimizer: Any, number: int) -> int:
    """!
    @brief The number of individuals of a training (train_ff -pop_size).
    @param spec      The choices: an object that may have "population", a
    whole number; without it there is one individual for each process.
    @param optimizer The optimizer, checked by processes.
    @param number    The number of processes, checked by processes.
    @return The number of individuals.
    @throws TrainingError if it is not a whole number within the limits,
    not even for the genetic algorithm, which pairs the individuals, more
    than the processes for Monte Carlo alone, or fewer than the processes
    and not a divisor of them, such that each individual gets as many.
    """
    value = spec.get("population") if isinstance(spec, Mapping) else None
    size = number if value is None else value
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_POPULATION:
        raise TrainingError("The number of individuals is between 1 and %d" % MAX_POPULATION)
    name = DEFAULT_OPTIMIZER if optimizer is None else optimizer
    if OPTIMIZERS.get(name, False) and (size < 2 or size % 2):
        # train_ff makes the next generation from pairs of individuals
        raise TrainingError("The optimizer %s needs an even number of individuals, two at least%s"
                            % (name, "" if value is not None else
                               ", and without a number of individuals it has one for each process"))
    if size > number and not OPTIMIZERS.get(name, False):
        raise TrainingError("Monte Carlo alone has at most one individual for each process, "
                            "not %d individuals on %d processes" % (size, number))
    if size < number and number % size:
        raise TrainingError("With %d individuals the number of processes is a multiple of %d, "
                            "such that each individual gets as many, not %d" % (size, size, number))
    return size


def settings(chosen: Mapping[str, Any], optimizer: Any) -> None:
    """!
    @brief Check the settings of a training that depend on each other and
    on the optimizer. That each is a number within its limits is checked
    with the other options.
    @param chosen    The options of train_ff that were given, by name.
    @param optimizer The optimizer, checked by processes.
    @throws TrainingError if the annealing ends before it starts, if only
    its end is given, since without a start there is no annealing, if the
    number of crossover points is not a whole number, or for a setting that
    the optimizer does not use: annealing for the genetic algorithm alone,
    annealing over the generations for another optimizer than HYBRID, and
    crossover and mutation for Monte Carlo alone.
    """
    name = DEFAULT_OPTIMIZER if optimizer is None else optimizer
    genetic = OPTIMIZERS.get(name, False)
    for pair, what in _ANNEALING_NAMES.items():
        begin, end = (chosen.get(key) for key in pair)
        if begin is None and end is None:
            continue
        if name == "GA":
            raise TrainingError("The optimizer GA has no Monte Carlo steps, so no annealing")
        if pair == GLOBAL_ANNEALING and name != "HYBRID":
            raise TrainingError("Annealing over the generations is for the optimizer HYBRID, "
                                "not for %s" % name)
        if begin is None:
            # train_ff does not anneal without a start
            raise TrainingError("Give where %s starts as well as where it ends" % what)
        if end is not None and end < begin:
            raise TrainingError("The end of %s (%g) is before its start (%g)"
                                % (what, end, begin))
    given = [key for key in GENETIC if chosen.get(key) is not None]
    if given and not genetic:
        raise TrainingError("Monte Carlo alone has no crossover or mutation (option %s)"
                            % ", ".join(given))
    crossovers = chosen.get("n_crossovers")
    if crossovers is not None and (isinstance(crossovers, bool)
                                   or not isinstance(crossovers, int)):
        raise TrainingError("The number of crossover points is a whole number")


def continued(spec: Any) -> Optional[str]:
    """!
    @brief The earlier training of which this one continues the population.
    @param spec The choices: an object that may have "continue", the
    identifier of the calculation.
    @return The identifier, None if the training starts anew.
    @throws TrainingError if it is not an identifier.
    """
    earlier = spec.get("continue") if isinstance(spec, Mapping) else None
    if earlier is None:
        return None
    if not isinstance(earlier, str) or not earlier:
        raise TrainingError("The training to continue is given by its identifier")
    return earlier


def pool_size(path: "os.PathLike[str]") -> int:
    """!
    @brief The number of individuals in a gene pool that train_ff wrote
    (-gpout), which has a line for each of them.
    @param path The file.
    @return The number of lines that are not empty.
    @throws TrainingError if the file cannot be read or is too large to be
    a gene pool.
    """
    try:
        if os.path.getsize(path) > MAX_POOL_BYTES:
            raise TrainingError("The population of the earlier training is too large")
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError as exc:
        raise TrainingError("The population of the earlier training cannot be read") from exc


def gene_pool(optimizer: Any, number: int, pool: Optional["os.PathLike[str]"]) -> Dict[str, Any]:
    """!
    @brief The option with which train_ff continues from the population of
    an earlier training.

    train_ff reads a gene pool for the genetic algorithm with or without
    Monte Carlo, not for Monte Carlo alone, and the pool must have as many
    individuals as the training has.
    @param optimizer The optimizer of this training.
    @param number    Its number of individuals.
    @param pool      The gene pool of the earlier training, None if that
    is not known or has none.
    @return The options.
    @throws TrainingError if the training cannot continue from it.
    """
    if pool is None:
        raise TrainingError("The training to continue is not known, or it has no population: "
                            "a training with Monte Carlo alone has none. Start from its "
                            "force field instead")
    if not OPTIMIZERS.get(optimizer, False):
        raise TrainingError("Monte Carlo alone does not continue from a population. Start "
                            "from the trained force field instead, without random values")
    size = pool_size(pool)
    if size != number:
        raise TrainingError("The earlier training had %d individuals, so this one needs %d "
                            "individuals, not %d" % (size, size, number))
    return {"gpin": Path(pool)}


def max_hours(seconds: float) -> float:
    """!
    @brief The time that train_ff gets to train (-maxh) in a job that may
    take a given time. What is left is for what comes after the training:
    the evaluation of the trained force field and writing the results. That
    is a tenth of the time, two minutes at least and half an hour at most,
    and never more than half of the time.
    @param seconds The longest time the job may take.
    @return The time in hours.
    """
    left = min(max(0.1 * seconds, 120.0), 1800.0, 0.5 * seconds)
    return round((seconds - left) / 3600.0, 5)


def digest(data: Any) -> Any:
    """!
    @brief What of the result of train_ff goes to the browser after a
    training: that of an evaluation, the parameters that were trained and
    the summary of the sensitivity analysis.
    @param data The result of train_ff.
    @return The statistics, the outliers and the compounds as for an
    evaluation, with "parameters", a list with for each parameter its kind
    ("class"), its name and its best value with the spread over the
    individuals, "sensitivity", the numbers of the analysis, and, for a
    training with a time limit, "time_limit" with whether it was reached.
    """
    if not isinstance(data, Mapping):
        return data
    made = evaluation.digest(data)
    parameters: List[Dict[str, Any]] = []
    listed = data.get("parameters") or {}
    if isinstance(listed, Mapping):
        # The keys are the numbers of the parameters
        def order(key: Any) -> Any:
            return (0, int(key)) if str(key).isdigit() else (1, str(key))
        listed = [listed[key] for key in sorted(listed, key=order)]
    for parameter in listed:
        if isinstance(parameter, Mapping):
            parameters.append({key: parameter.get(key) for key in
                               ("class", "name", "best", "min", "max", "mean", "stdev",
                                "median")})
    made["parameters"] = parameters
    sensitivity = data.get("sensitivity")
    made["sensitivity"] = {key: value for key, value in sensitivity.items()
                           if not isinstance(value, (Mapping, list))} \
        if isinstance(sensitivity, Mapping) else {}
    # Whether the training stopped because its time was up, see max_hours
    limit = data.get("time_limit")
    if isinstance(limit, Mapping):
        hours = limit.get("limit")
        made["time_limit"] = {"reached": limit.get("reached") == "yes",
                              "hours": hours.get("value") if isinstance(hours, Mapping) else None}
    return made


def describe() -> Dict[str, Any]:
    """!
    @brief What the browser needs to know to build the form for a training.
    @return The optimizers with whether they need more than one process,
    the optimizer that is used when none is chosen, the largest part of
    the compounds for testing and the names of the settings that can be
    given, see SETTINGS.
    """
    return {"optimizers": [{"name": name, "parallel": parallel}
                           for name, parallel in OPTIMIZERS.items()],
            "default_optimizer": DEFAULT_OPTIMIZER, "max_test": MAX_TEST,
            "settings": list(SETTINGS)}
