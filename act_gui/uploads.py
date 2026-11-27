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
@brief Files that users upload: molecules, force fields and molprop files.

An uploaded file is given to a program written in C++, so it is treated
with suspicion. The name the user gave is only a label: on disk the file
has a name made by the server. The size is limited, the extension must be
one that the kind of file can have, the content must be text and must look
like the format, and XML files cannot declare a document type, with which a
file could make the reader fetch other files or addresses.

A molprop file can be gigabytes. A file is therefore never in memory as a
whole: what the browser sends is written to a file in the store as it comes
in, that file is checked piece by piece, and it is kept by giving it its
name. Only the files of molecules, which are small, are read as a whole.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

## The extensions that each kind of file can have
KINDS: Dict[str, tuple] = {
    "molecule": (".sdf", ".pdb", ".xyz"),
    "forcefield": (".xml",),
    "molprop": (".xml",),
}

## What an identifier of an upload looks like
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

## The document types that ACT writes at the top of its XML files, by kind.
## They name a file next to the XML file and define nothing themselves.
_DOCTYPES: Dict[str, str] = {
    "forcefield": "alexandria_chemistry.dtd",
    "molprop": "molprops.dtd",
}
## Where a document type and an entity start, in the bytes of a file
_DOCTYPE_START = re.compile(rb"<!doctype", re.IGNORECASE)
_ENTITY_START = re.compile(rb"<!entity", re.IGNORECASE)
## How much of the end of a piece of a file is looked at again with the
## next piece: one character less than the longest of the two above
_CARRY = len("<!doctype") - 1
## The size in bytes of the pieces in which an XML file is checked
PIECE_BYTES = 1024 * 1024
## The number of characters at the start of an XML file in which its
## document type is looked for
HEAD_CHARS = 65536
## The extension of a file that is coming in or is not checked yet
PART = ".part"
## A document type as ACT writes it
_DOCTYPE = re.compile(r'<!DOCTYPE\s+(\S+)\s+PUBLIC\s+"([^"<>\[\]]*)"\s+"([^"<>\[\]]*)"\s*>')


class UploadError(ValueError):
    """!
    @brief A file that is not accepted. The message is for the user.
    """


@dataclass(frozen=True)
class Upload:
    """!
    @brief A file that was accepted.
    """
    ## The identifier, with which the file is referred to from then on
    id: str
    ## The name the user gave, cleaned, only for display
    label: str
    ## molecule, forcefield or molprop
    kind: str
    ## The extension, with the dot
    extension: str
    ## The size in bytes
    size: int


def clean_label(name: str) -> str:
    """!
    @brief Make a name from the user safe to show and to store.
    @param name The file name as the browser sent it.
    @return The last part of the path, with only letters, digits, spaces
    and a few punctuation marks, at most 100 characters.
    """
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(c if (c.isalnum() or c in " ._-+()#,") else "_" for c in name)
    name = name.strip(" .")
    return name[:100] or "file"


def _check_text(data: bytes) -> str:
    """!
    @brief Check that the content is text.
    @param data The content.
    @return The text.
    @throws UploadError if it is not text in UTF-8 or has null characters.
    """
    if b"\0" in data:
        raise UploadError("The file is not a text file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError("The file is not a text file in UTF-8") from exc


def _check_molecule(extension: str, text: str) -> None:
    """!
    @brief Check that the content of a file with a molecule looks like the
    format of the extension.
    @param extension The extension, with the dot.
    @param text      The content.
    @throws UploadError if it does not.
    """
    lines = text.splitlines()
    if not text.strip():
        raise UploadError("The file is empty")
    if extension == ".sdf":
        if not any(("V2000" in line or "V3000" in line) for line in lines[:10]):
            raise UploadError("The file is not an sdf file: there is no counts line")
    elif extension == ".pdb":
        if not any(line.startswith(("ATOM", "HETATM")) for line in lines):
            raise UploadError("The file is not a pdb file: there are no atoms")
    elif extension == ".xyz":
        try:
            natom = int(lines[0].split()[0])
        except (ValueError, IndexError) as exc:
            raise UploadError("The file is not an xyz file: the first line must be "
                              "the number of atoms") from exc
        if natom < 1 or len(lines) < natom + 2:
            raise UploadError("The file is not an xyz file: there are fewer lines than atoms")


def _check_xml(kind: str, path: Path) -> None:
    """!
    @brief Check that a file is text and looks like an XML file of ACT.
    A molprop file can be gigabytes: the file is read piece by piece, such
    that the check needs the memory of one piece, whatever the size of the
    file. What is looked for has ASCII characters only, which in UTF-8 are
    never part of another character, so the search is done in the bytes.
    @param kind forcefield or molprop.
    @param path The file.
    @throws UploadError if it does not.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    # The end of the piece before, such that what is looked for is found
    # when it is in two pieces: one character less than the longest of them,
    # so that nothing is counted twice
    carry = b""
    head = ""
    started = False
    doctypes = 0
    with open(path, "rb") as stream:
        while True:
            piece = stream.read(PIECE_BYTES)
            if not piece:
                break
            if b"\0" in piece:
                raise UploadError("The file is not a text file")
            try:
                text = decoder.decode(piece)
            except UnicodeDecodeError as exc:
                raise UploadError("The file is not a text file in UTF-8") from exc
            if len(head) < HEAD_CHARS:
                head = (head + text)[:HEAD_CHARS]
            if not started:
                first = text.lstrip()
                if first:
                    started = True
                    if not first.startswith("<"):
                        raise UploadError("The file is not an XML file")
            # A document type is how a file makes an XML reader fetch other
            # files or addresses, or expand to gigabytes. Only the one that
            # ACT writes itself is accepted: it names the description of the
            # format and defines no entities.
            window = carry + piece
            if _ENTITY_START.search(window):
                raise UploadError("XML files with entities are not accepted")
            doctypes += len(_DOCTYPE_START.findall(window))
            carry = window[-_CARRY:]
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UploadError("The file is not a text file in UTF-8") from exc
    if not started:
        raise UploadError("The file is empty")
    if doctypes:
        # The document type is before the first element, so at the start
        match = _DOCTYPE.search(head)
        names = set(match.groups()) if match else set()
        if doctypes > 1 or names != {_DOCTYPES[kind]}:
            raise UploadError("XML files with another document type than that of ACT "
                              "are not accepted")
    expected = "<alexandria" if kind == "forcefield" else "<molecules"
    if expected not in head[:5000].lower():
        raise UploadError("The file is not %s file of ACT"
                          % ("a force field" if kind == "forcefield" else "a molprop"))


class UploadStore:
    """!
    @brief Keeps the uploaded files in a directory.
    """

    def __init__(self, directory: Path, max_bytes: int,
                 limits: Optional[Mapping[str, int]] = None):
        """!
        @brief Make the store.
        @param directory Where the files are kept, made if it does not exist.
        @param max_bytes The largest file that is accepted.
        @param limits    Another largest size for some kinds of files, by
        kind: molprop files are larger than molecules.
        """
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._limits = dict(limits or {})
        # What a server that was stopped during an upload left behind
        for left in self._dir.glob("*" + PART):
            left.unlink(missing_ok=True)

    def limit(self, kind: str) -> int:
        """!
        @brief The largest file of a kind that is accepted.
        @param kind molecule, forcefield or molprop.
        @return The size in bytes.
        """
        return self._limits.get(kind, self._max_bytes)

    def named(self, name: str, kind: str) -> Tuple[str, str]:
        """!
        @brief Check the kind and the name of a file, which can be done
        before any of its content is there.
        @param name The file name the user gave.
        @param kind molecule, forcefield or molprop.
        @return The label and the extension, with the dot.
        @throws UploadError for an unknown kind, or an extension that the
        kind of file cannot have.
        """
        if kind not in KINDS:
            raise UploadError("Unknown kind of file '%s'" % kind)
        label = clean_label(name)
        extension = Path(label).suffix.lower()
        if extension not in KINDS[kind]:
            raise UploadError("A %s file must have the extension %s"
                              % (kind, " or ".join(KINDS[kind])))
        return label, extension

    def incoming(self) -> Path:
        """!
        @brief Where to write a file that is coming in, in the directory of
        the store, such that keeping it is giving it its name.
        @return A path that is not in use, see keep().
        """
        return self._dir / (uuid.uuid4().hex + PART)

    def keep(self, name: str, kind: str, incoming: Path) -> Upload:
        """!
        @brief Check a file that was written to a path of incoming() and
        keep it. The file is moved or, if it is not accepted, removed.
        @param name     The file name the user gave.
        @param kind     molecule, forcefield or molprop.
        @param incoming The file.
        @return The upload, with the identifier to refer to it.
        @throws UploadError if the file is not accepted.
        """
        try:
            label, extension = self.named(name, kind)
            size = incoming.stat().st_size
            if size > self.limit(kind):
                raise UploadError("The file is larger than %d kB" % (self.limit(kind) // 1024))
            if extension == ".xml":
                _check_xml(kind, incoming)
            else:
                _check_molecule(extension, _check_text(incoming.read_bytes()))
            upload = Upload(id=uuid.uuid4().hex, label=label, kind=kind,
                            extension=extension, size=size)
            os.replace(incoming, self.path(upload))
            (self._dir / (upload.id + ".json")).write_text(json.dumps(asdict(upload)))
            return upload
        finally:
            incoming.unlink(missing_ok=True)

    def save(self, name: str, kind: str, data: bytes) -> Upload:
        """!
        @brief Check a file that is in memory and keep it, see keep() for
        one that is not.
        @param name The file name the user gave.
        @param kind molecule, forcefield or molprop.
        @param data The content.
        @return The upload, with the identifier to refer to it.
        @throws UploadError if the file is not accepted.
        """
        self.named(name, kind)
        if len(data) > self.limit(kind):
            raise UploadError("The file is larger than %d kB" % (self.limit(kind) // 1024))
        incoming = self.incoming()
        incoming.write_bytes(data)
        return self.keep(name, kind, incoming)

    def get(self, upload_id: str) -> Optional[Upload]:
        """!
        @brief Look up an upload.
        @param upload_id The identifier.
        @return The upload, None if there is none with this identifier.
        """
        if not isinstance(upload_id, str) or not _ID_PATTERN.fullmatch(upload_id):
            return None
        meta = self._dir / (upload_id + ".json")
        if not meta.is_file():
            return None
        try:
            return Upload(**json.loads(meta.read_text()))
        except (ValueError, TypeError):
            return None

    def path(self, upload: Upload) -> Path:
        """!
        @brief Where the content of an upload is.
        @param upload The upload.
        @return The path, with a name made by the server.
        """
        return self._dir / (upload.id + upload.extension)
