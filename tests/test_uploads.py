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
@brief Tests of what is accepted as an uploaded file.
"""
import tempfile
import unittest
from pathlib import Path

from act_gui.uploads import UploadError, UploadStore, clean_label

## A small sdf file
SDF = b"""water

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 O   0  0
    0.9572    0.0000    0.0000 H   0  0
   -0.2400    0.9266    0.0000 H   0  0
  1  2  1  0
  1  3  1  0
M  END
$$$$
"""
## A small pdb file
PDB = b"HETATM    1  O   HOH     1       0.000   0.000   0.000\nEND\n"
## A small xyz file
XYZ = b"3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"
## The start of a force field and of a molprop file
FF = b'<?xml version="1.0"?>\n<alexandria_chemistry>\n</alexandria_chemistry>\n'
MP = b'<?xml version="1.0"?>\n<molecules>\n</molecules>\n'


class UploadTest(unittest.TestCase):
    """!
    @brief Files are checked before they are kept.
    """

    def setUp(self):
        """!
        @brief Make a store that takes files up to 2 kB.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = UploadStore(self.dir, max_bytes=2048)

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def test_good_files_are_kept(self):
        """!
        @brief The formats of ACT are accepted and can be found again.
        """
        for name, kind, data in (("water.sdf", "molecule", SDF), ("water.pdb", "molecule", PDB),
                                 ("water.xyz", "molecule", XYZ), ("ff.xml", "forcefield", FF),
                                 ("mp.xml", "molprop", MP), ("WATER.SDF", "molecule", SDF)):
            upload = self.store.save(name, kind, data)
            self.assertEqual(kind, upload.kind)
            self.assertEqual(len(data), upload.size)
            self.assertEqual(data, self.store.path(upload).read_bytes())
            self.assertEqual(upload, self.store.get(upload.id))

    def test_name_on_disk_is_made_by_the_server(self):
        """!
        @brief The name of the user is a label only and cannot leave the directory.
        """
        upload = self.store.save("../../etc/passwd.sdf", "molecule", SDF)
        self.assertEqual("passwd.sdf", upload.label)
        path = self.store.path(upload)
        self.assertEqual(self.dir.resolve(), path.parent.resolve())
        self.assertEqual(upload.id + ".sdf", path.name)
        self.assertNotIn("passwd", path.name)

    def test_labels_are_cleaned(self):
        """!
        @brief Odd characters, paths and long names are removed from labels.
        """
        self.assertEqual("b.sdf", clean_label("a/b.sdf"))
        self.assertEqual("b.sdf", clean_label("C:\\\\a\\\\b.sdf"))
        self.assertEqual("a_script_.sdf", clean_label("a<script>.sdf"))
        self.assertEqual("file", clean_label("..."))
        self.assertLessEqual(len(clean_label("x" * 500 + ".sdf")), 100)

    def test_wrong_files_are_refused(self):
        """!
        @brief Size, extension, content and kind are checked.
        """
        wrong = [
            ("big.sdf", "molecule", SDF + b" " * 4000, "larger"),
            ("water.exe", "molecule", SDF, "extension"),
            ("water.sdf", "program", SDF, "kind"),
            ("water.sdf", "molecule", b"\x00\x01\x02", "text"),
            ("water.sdf", "molecule", b"\xff\xfe not utf8", "text"),
            ("water.sdf", "molecule", b"just some text\n", "sdf"),
            ("water.sdf", "molecule", b"   \n", "empty"),
            ("water.pdb", "molecule", b"REMARK nothing\n", "pdb"),
            ("water.xyz", "molecule", b"three\nwater\n", "xyz"),
            ("water.xyz", "molecule", b"30\nwater\nO 0 0 0\n", "xyz"),
            ("ff.xml", "forcefield", b"not xml", "XML"),
            ("ff.xml", "forcefield", MP, "force field"),
            ("mp.xml", "molprop", FF, "molprop"),
            ("water.xml", "molecule", FF, "extension"),
        ]
        for name, kind, data, word in wrong:
            with self.assertRaises(UploadError, msg=name) as caught:
                self.store.save(name, kind, data)
            self.assertIn(word, str(caught.exception))
        # Nothing was kept
        self.assertEqual([], list(self.dir.iterdir()))

    def test_xml_with_document_type_is_refused(self):
        """!
        @brief A file cannot make the XML reader fetch other files: no
        entities, and no document type but the one ACT writes.
        """
        ours = b'<!DOCTYPE alexandria_chemistry.dtd PUBLIC "alexandria_chemistry.dtd" ' \
               b'"alexandria_chemistry.dtd">\n'
        for evil in (b'<?xml version="1.0"?>\n<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
                     b'<alexandria_chemistry>&x;</alexandria_chemistry>',
                     b'<?xml version="1.0"?>\n<!doctype a>\n<alexandria_chemistry/>',
                     b'<alexandria_chemistry><!ENTITY lol "lol"></alexandria_chemistry>',
                     # Another file or address than the one ACT names
                     b'<!DOCTYPE alexandria_chemistry.dtd PUBLIC "alexandria_chemistry.dtd" '
                     b'"http://example.org/evil.dtd">\n<alexandria_chemistry/>',
                     b'<!DOCTYPE alexandria_chemistry.dtd SYSTEM "/etc/passwd">\n'
                     b'<alexandria_chemistry/>',
                     b'<!DOCTYPE alexandria_chemistry.dtd PUBLIC "alexandria_chemistry.dtd" '
                     b'"alexandria_chemistry.dtd" [<!ELEMENT a ANY>]>\n<alexandria_chemistry/>',
                     # Two of them, of which the first is fine
                     ours + b'<!DOCTYPE b SYSTEM "x">\n<alexandria_chemistry/>',
                     # The document type of the other kind of file
                     b'<!DOCTYPE molprops.dtd PUBLIC "molprops.dtd" "molprops.dtd">\n'
                     b'<alexandria_chemistry/>'):
            with self.assertRaises(UploadError, msg=evil) as caught:
                self.store.save("ff.xml", "forcefield", evil)
            self.assertRegex(str(caught.exception), "document type|entities")
        self.assertEqual([], list(self.dir.iterdir()))

    def test_files_as_act_writes_them_are_kept(self):
        """!
        @brief Force field and molprop files of ACT start with a document
        type that names the description of their format. They are accepted.
        """
        head = b'<?xml version="1.0" encoding="ISO-8859-1"?>\n'
        forcefield = head + (b'<!DOCTYPE alexandria_chemistry.dtd PUBLIC '
                             b'"alexandria_chemistry.dtd" "alexandria_chemistry.dtd">\n'
                             b'<alexandria_chemistry>\n</alexandria_chemistry>\n')
        molprop = head + (b'<!DOCTYPE molprops.dtd PUBLIC "molprops.dtd" "molprops.dtd">\n'
                          b'<molecules>\n</molecules>\n')
        self.assertEqual("forcefield", self.store.save("ff.xml", "forcefield", forcefield).kind)
        self.assertEqual("molprop", self.store.save("mp.xml", "molprop", molprop).kind)

    def test_unknown_identifiers(self):
        """!
        @brief Identifiers that were not given out find nothing.
        """
        for wrong in ("", "abc", "../" + "a" * 29, "g" * 32, None, 12, "a" * 32):
            self.assertIsNone(self.store.get(wrong))


if __name__ == "__main__":
    unittest.main()
