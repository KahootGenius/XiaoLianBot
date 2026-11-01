"""The folder of character reference pictures."""
import io
import os
import shutil
import tempfile
import unittest
from modules.ai import gallery

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32


class GalleryTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)

    def add(self, file, data=PNG):
        with open(os.path.join(self.folder, file), "wb") as f:
            f.write(data)


class LoadTest(GalleryTestCase):
    def test_a_file_is_named_after_its_character(self):
        self.add("遐蝶.jpg", JPEG)
        self.add("芙宁娜（原神）.png")
        references, problems = gallery.load(self.folder)
        self.assertEqual(problems, [])
        self.assertEqual([name for name, _ in references], ["芙宁娜（原神）", "遐蝶"])
        self.assertTrue(references[1][1].startswith("data:image/jpeg;base64,"))

    def test_they_come_in_the_same_order_every_time(self):
        for file in ("b.png", "a.png", "c.png"):
            self.add(file)
        self.assertEqual([name for name, _ in gallery.load(self.folder)[0]], ["a", "b", "c"])

    def test_the_format_comes_from_the_file_not_its_name(self):
        self.add("遐蝶.png", JPEG)
        self.assertTrue(gallery.load(self.folder)[0][0][1].startswith("data:image/jpeg"))

    def test_a_bad_file_is_left_out_and_named(self):
        self.add("说明.txt", b"hello")
        self.add("坏图.png", b"not a picture")
        self.add("太大.png", PNG + b"\0" * gallery.MAX_BYTES)
        self.add(f"{'长' * 31}.png")
        self.add(" .png")
        self.add("好图.png")
        references, problems = gallery.load(self.folder)
        self.assertEqual([name for name, _ in references], ["好图"])
        self.assertEqual(len(problems), 5)
        text = "\n".join(problems)
        for word in ("说明.txt", "坏图.png", "太大.png：超过 500 KB", "sips -Z 1000"):
            self.assertIn(word, text)

    def test_hidden_files_and_folders_are_ignored(self):
        self.add(".DS_Store", b"junk")
        os.mkdir(os.path.join(self.folder, "旧的"))
        self.assertEqual(gallery.load(self.folder), ([], []))

    def test_there_is_a_limit_on_pictures(self):
        for n in range(gallery.MAX_PICTURES + 2):
            self.add(f"角色{n:03}.png")
        references, problems = gallery.load(self.folder)
        self.assertEqual(len(references), gallery.MAX_PICTURES)
        self.assertEqual(len(problems), 2)

    def test_no_folder_means_no_gallery(self):
        self.assertEqual(gallery.load(os.path.join(self.folder, "nothing")), ([], []))


class CheckCommandTest(GalleryTestCase):
    def test_it_lists_the_characters(self):
        self.add("遐蝶.png", PNG + b"\0" * 2048)
        self.add("芙宁娜.png")
        out = io.StringIO()
        self.assertEqual(gallery.main([], self.folder, out), 0)
        self.assertIn("遐蝶  2 KB", out.getvalue())
        self.assertIn("2 characters, 2 KB in all, 0 problems", out.getvalue())

    def test_it_reports_problems(self):
        self.add("说明.txt")
        out = io.StringIO()
        self.assertEqual(gallery.main([], self.folder, out), 1)
        self.assertIn("!! 说明.txt", out.getvalue())


if __name__ == "__main__":
    unittest.main()
