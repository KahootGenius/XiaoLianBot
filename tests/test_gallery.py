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
        self.assertEqual([name for name, *_ in references], ["芙宁娜（原神）", "遐蝶"])
        self.assertTrue(references[1][1].startswith("data:image/jpeg;base64,"))

    def test_they_come_in_the_same_order_every_time(self):
        for file in ("b.png", "a.png", "c.png"):
            self.add(file)
        self.assertEqual([name for name, *_ in gallery.load(self.folder)[0]], ["a", "b", "c"])

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
        self.assertEqual([name for name, *_ in references], ["好图"])
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


class NotesTest(GalleryTestCase):
    def notes(self, text):
        self.add("遐蝶.png")
        self.add("芙宁娜.png")
        self.add(gallery.NOTES_FILE, text.encode("utf-8") if isinstance(text, str) else text)
        return gallery.load(self.folder)

    def test_a_note_goes_with_its_character(self):
        references, problems = self.notes('遐蝶: " 浅紫长发 "\n')
        self.assertEqual(problems, [])
        self.assertEqual([(name, note) for name, _, note in references], [("芙宁娜", ""), ("遐蝶", "浅紫长发")])

    def test_notes_are_optional(self):
        self.add("遐蝶.png")
        self.assertEqual(gallery.load(self.folder)[0][0][2], "")

    def test_an_empty_file_is_fine(self):
        self.assertEqual(self.notes("")[1], [])

    def test_a_note_for_a_character_without_a_picture_is_named(self):
        references, problems = self.notes("遐蝶: 浅紫长发\n蕾米艾尔: 粉红短发\n")
        self.assertEqual(references[1][2], "浅紫长发")
        self.assertEqual(len(problems), 1)
        self.assertIn("蕾米艾尔", problems[0])

    def test_a_bad_note_is_left_out_and_named(self):
        references, problems = self.notes(f"遐蝶: {'长' * (gallery.MAX_NOTE + 1)}\n芙宁娜: 3\n")
        self.assertEqual([note for _, _, note in references], ["", ""])
        self.assertEqual(len(problems), 2)
        self.assertIn(f"不超过 {gallery.MAX_NOTE} 个字", problems[0])

    def test_a_blank_note_is_named_too(self):
        references, problems = self.notes('遐蝶: "  "\n')
        self.assertEqual(references[1][2], "")
        self.assertEqual(len(problems), 1)

    def test_the_longest_note_allowed_is_kept(self):
        note = "长" * gallery.MAX_NOTE
        self.assertEqual(self.notes(f"遐蝶: {note}\n")[0][1][2], note)

    def test_a_broken_file_costs_the_notes_not_the_pictures(self):
        for text in ("遐蝶: [\n", "- 遐蝶\n", "只有一句话\n", "遐蝶: 浅紫".encode("gbk")):
            references, problems = self.notes(text)
            self.assertEqual([note for _, _, note in references], ["", ""], text)
            self.assertEqual(len(problems), 1, text)
            self.assertIn(gallery.NOTES_FILE, problems[0])


class CheckCommandTest(GalleryTestCase):
    def test_it_lists_the_characters(self):
        self.add("遐蝶.png", PNG + b"\0" * 2048)
        self.add("芙宁娜.png")
        out = io.StringIO()
        self.assertEqual(gallery.main([], self.folder, out), 0)
        self.assertIn("遐蝶  2 KB\n", out.getvalue())
        self.assertIn("2 characters, 2 KB in all, 0 problems", out.getvalue())

    def test_it_shows_the_notes(self):
        self.add("遐蝶.png", PNG + b"\0" * 2048)
        self.add(gallery.NOTES_FILE, "遐蝶: 浅紫长发".encode("utf-8"))
        out = io.StringIO()
        self.assertEqual(gallery.main([], self.folder, out), 0)
        self.assertIn("遐蝶  2 KB  浅紫长发\n", out.getvalue())

    def test_it_reports_problems(self):
        self.add("说明.txt")
        out = io.StringIO()
        self.assertEqual(gallery.main([], self.folder, out), 1)
        self.assertIn("!! 说明.txt", out.getvalue())


if __name__ == "__main__":
    unittest.main()
