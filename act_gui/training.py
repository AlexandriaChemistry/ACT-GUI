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
(module evaluation). train_ff has one individual, a set of parameters, for
each of its processes, so the number of processes is the size of the
population; the genetic algorithm needs two at least.
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

## The optimizers of train_ff, and whether they need more than one process
OPTIMIZERS: Dict[str, bool] = {"MCMC": False, "HYBRID": True, "GA": True}

## The optimizer when none is chosen: the one that runs on one process
DEFAULT_OPTIMIZER = "MCMC"


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
        if not isinstance(name, str) or not _PARAMETER.match(name):
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
    @return The number of processes, which is the number of individuals.
    @throws TrainingError if it is not a number within the limits, or too
    small for the optimizer.
    """
    number = 1 if value is None else value
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= largest:
        raise TrainingError("The number of processes is between 1 and %d" % largest)
    name = DEFAULT_OPTIMIZER if optimizer is None else optimizer
    if name not in OPTIMIZERS:
        raise TrainingError("The optimizer is one of %s" % ", ".join(OPTIMIZERS))
    if OPTIMIZERS[name] and (number < 2 or number % 2):
        # train_ff makes the next generation from pairs of individuals
        raise TrainingError("The optimizer %s needs an even number of processes, two at least"
                            % name)
    return number


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
    individuals as the training has processes.
    @param optimizer The optimizer of this training.
    @param number    Its number of processes.
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
                            "processes, not %d" % (size, size, number))
    return {"gpin": Path(pool)}


def digest(data: Any) -> Any:
    """!
    @brief What of the result of train_ff goes to the browser after a
    training: that of an evaluation, the parameters that were trained and
    the summary of the sensitivity analysis.
    @param data The result of train_ff.
    @return The statistics, the outliers and the compounds as for an
    evaluation, with "parameters", a list with for each parameter its kind
    ("class"), its name and its best value with the spread over the
    individuals, and "sensitivity", the numbers of the analysis.
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
    return made


def describe() -> Dict[str, Any]:
    """!
    @brief What the browser needs to know to build the form for a training.
    @return The optimizers with whether they need more than one process,
    the optimizer that is used when none is chosen and the largest part of
    the compounds for testing.
    """
    return {"optimizers": [{"name": name, "parallel": parallel}
                           for name, parallel in OPTIMIZERS.items()],
            "default_optimizer": DEFAULT_OPTIMIZER, "max_test": MAX_TEST}
