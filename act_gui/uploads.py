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
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

## The extensions that each kind of file can have
KINDS: Dict[str, tuple] = {
    "molecule": (".sdf", ".pdb", ".xyz"),
    "forcefield": (".xml",),
    "molprop": (".xml",),
}

## What an identifier of an upload looks like
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


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


def _check_format(kind: str, extension: str, text: str) -> None:
    """!
    @brief Check that the content looks like the format of the extension.
    @param kind      The kind of file.
    @param extension The extension, with the dot.
    @param text      The content.
    @throws UploadError if it does not.
    """
    lines = text.splitlines()
    if not text.strip():
        raise UploadError("The file is empty")
    if extension == ".xml":
        head = text.lstrip()[:200]
        if not head.startswith("<"):
            raise UploadError("The file is not an XML file")
        # No document type: that is how a file makes an XML reader fetch
        # other files or addresses, or expand to gigabytes
        lowered = text.lower()
        if "<!doctype" in lowered or "<!entity" in lowered:
            raise UploadError("XML files with a document type or entities are not accepted")
        expected = "<alexandria" if kind == "forcefield" else "<molecules"
        if expected not in lowered[:5000]:
            raise UploadError("The file is not %s file of ACT"
                              % ("a force field" if kind == "forcefield" else "a molprop"))
    elif extension == ".sdf":
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


class UploadStore:
    """!
    @brief Keeps the uploaded files in a directory.
    """

    def __init__(self, directory: Path, max_bytes: int):
        """!
        @brief Make the store.
        @param directory Where the files are kept, made if it does not exist.
        @param max_bytes The largest file that is accepted.
        """
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes

    def save(self, name: str, kind: str, data: bytes) -> Upload:
        """!
        @brief Check a file and keep it.
        @param name The file name the user gave.
        @param kind molecule, forcefield or molprop.
        @param data The content.
        @return The upload, with the identifier to refer to it.
        @throws UploadError if the file is not accepted.
        """
        if kind not in KINDS:
            raise UploadError("Unknown kind of file '%s'" % kind)
        if len(data) > self._max_bytes:
            raise UploadError("The file is larger than %d kB" % (self._max_bytes // 1024))
        label = clean_label(name)
        extension = Path(label).suffix.lower()
        if extension not in KINDS[kind]:
            raise UploadError("A %s file must have the extension %s"
                              % (kind, " or ".join(KINDS[kind])))
        _check_format(kind, extension, _check_text(data))
        upload = Upload(id=uuid.uuid4().hex, label=label, kind=kind,
                        extension=extension, size=len(data))
        self.path(upload).write_bytes(data)
        (self._dir / (upload.id + ".json")).write_text(json.dumps(asdict(upload)))
        return upload

    def get(self, upload_id: str) -> Optional[Upload]:
        """!
        @brief Look up an upload.
        @param upload_id The identifier.
        @return The upload, None if there is none with this identifier.
        """
        if not isinstance(upload_id, str) or not _ID_PATTERN.match(upload_id):
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
