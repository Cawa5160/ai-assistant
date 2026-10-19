import os
import shutil
import tempfile
import unittest

import file_ops


class FileOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, "Documents")
        self.outside = os.path.join(self.tmp, "Elsewhere")
        self.trash = os.path.join(self.tmp, "trash")
        self.log = os.path.join(self.tmp, "undo.json")
        os.makedirs(self.root)
        os.makedirs(self.outside)
        self.roots = [self.root]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)       # только очистка тестовой папки

    def mk(self, name, folder=None, text="x"):
        p = os.path.join(folder or self.root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_rename_numbering_and_undo(self):
        a, b = self.mk("a.txt"), self.mk("b.txt")
        plan = file_ops.plan_rename([a, b], numbering="Doc {n:02d}", allowed_roots=self.roots)
        self.assertEqual([os.path.basename(o.dst) for o in plan.ops], ["Doc 01.txt", "Doc 02.txt"])
        res = file_ops.apply(plan, self.log, allowed_roots=self.roots)
        self.assertEqual(res.done, 2)
        self.assertFalse(os.path.exists(a))
        u = file_ops.undo(self.log)
        self.assertEqual(u.restored, 2)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_rename_conflict(self):
        a, b = self.mk("a.txt"), self.mk("b.txt")
        plan = file_ops.plan_rename([a], pattern="a", replace="b", allowed_roots=self.roots)
        self.assertTrue(plan.ops[0].blocked)
        self.assertIn("конфликт", plan.ops[0].reason)
        res = file_ops.apply(plan, self.log, allowed_roots=self.roots)
        self.assertEqual((res.done, res.skipped), (0, 1))
        self.assertTrue(os.path.exists(a))

    def test_rename_two_to_one_name(self):
        a, b = self.mk("a1.txt"), self.mk("a2.txt")
        plan = file_ops.plan_rename([a, b], pattern="a1", replace="z", allowed_roots=self.roots)
        plan2 = file_ops.plan_rename([a, b], regex=True, pattern=r"\d", replace="", allowed_roots=self.roots)
        self.assertEqual(len(plan2.runnable), 1)
        self.assertEqual(len(plan2.blocked), 1)

    def test_move_creates_dir_and_undo_removes_it(self):
        a = self.mk("a.txt")
        dest = os.path.join(self.root, "New")
        plan = file_ops.plan_move([a], dest, allowed_roots=self.roots)
        file_ops.apply(plan, self.log, allowed_roots=self.roots)
        self.assertTrue(os.path.exists(os.path.join(dest, "a.txt")))
        file_ops.undo(self.log)
        self.assertTrue(os.path.exists(a))
        self.assertFalse(os.path.exists(dest))

    def test_move_outside_allowed_blocked(self):
        a = self.mk("a.txt")
        plan = file_ops.plan_move([a], self.outside, allowed_roots=self.roots)
        self.assertFalse(plan.ops)
        self.assertTrue(plan.warnings)
        ext = self.mk("e.txt", self.outside)
        plan = file_ops.plan_move([ext], self.root, allowed_roots=self.roots)
        self.assertTrue(plan.ops[0].blocked)
        self.assertEqual(file_ops.apply(plan, self.log, allowed_roots=self.roots).done, 0)
        self.assertTrue(os.path.exists(ext))

    def test_root_and_system_untouchable(self):
        plan = file_ops.plan_delete([self.root], allowed_roots=self.roots)
        self.assertTrue(plan.ops[0].blocked)
        ai = self.mk("settings.json", os.path.join(self.root, "AppData", "Roaming", "AI_Assistant"))
        self.assertTrue(file_ops.plan_delete([ai], allowed_roots=self.roots).ops[0].blocked)
        self.assertTrue(file_ops.plan_delete([os.path.abspath(os.sep)], allowed_roots=self.roots).ops[0].blocked)

    def test_symlink_escape_blocked(self):
        ext = self.mk("secret.txt", self.outside)
        link = os.path.join(self.root, "lnk.txt")
        try:
            os.symlink(ext, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink недоступен")
        plan = file_ops.plan_delete([link], allowed_roots=self.roots)
        self.assertTrue(plan.ops[0].blocked)
        dirlink = os.path.join(self.root, "dl")
        os.symlink(self.outside, dirlink)
        plan = file_ops.plan_move([self.mk("a.txt")], dirlink, allowed_roots=self.roots)
        self.assertFalse(plan.ops)

    def test_delete_goes_to_trash_and_restores(self):
        a = self.mk("a.txt", text="data")
        plan = file_ops.plan_delete([a], allowed_roots=self.roots)
        res = file_ops.apply(plan, self.log, allowed_roots=self.roots, trash_dir=self.trash)
        self.assertEqual(res.done, 1)
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(os.path.join(self.trash, "a.txt")))
        self.assertEqual(file_ops.undo(self.log).restored, 1)
        with open(a) as f:
            self.assertEqual(f.read(), "data")

    def test_no_delete_without_trash(self):
        a = self.mk("a.txt")
        folder = self.mk("d/f.txt")
        def broken(_p):
            raise file_ops.TrashUnavailable("нет корзины")
        plan = file_ops.plan_delete([a, os.path.dirname(folder)], allowed_roots=self.roots)
        res = file_ops.apply(plan, self.log, allowed_roots=self.roots, trash_fn=broken)
        self.assertEqual(res.done, 0)
        self.assertEqual(len(res.failed), 2)
        self.assertTrue(os.path.exists(a) and os.path.exists(folder))
        self.assertFalse(file_ops.can_undo(self.log))
        # ни одной операции удаления «насовсем» в API
        self.assertEqual(file_ops.KINDS, ("rename", "move", "delete"))
        import inspect
        self.assertNotIn("rmtree", inspect.getsource(file_ops).replace("rmtree/", "").replace("никакого rmtree", ""))

    def test_organize_by_type(self):
        self.mk("p.jpg"); self.mk("r.PDF"); self.mk("x.weird"); self.mk("m.mp3"); self.mk(".hidden")
        plan = file_ops.plan_organize(self.root, "type", allowed_roots=self.roots)
        dst = {os.path.basename(o.src): os.path.relpath(o.dst, self.root) for o in plan.ops}
        self.assertEqual(dst["p.jpg"], os.path.join("Изображения", "p.jpg"))
        self.assertEqual(dst["r.PDF"], os.path.join("Документы", "r.PDF"))
        self.assertEqual(dst["x.weird"], os.path.join("Прочее", "x.weird"))
        self.assertEqual(dst["m.mp3"], os.path.join("Музыка", "m.mp3"))
        self.assertNotIn(".hidden", dst)
        res = file_ops.apply(plan, self.log, allowed_roots=self.roots)
        self.assertEqual(res.done, 4)
        file_ops.undo(self.log)
        self.assertTrue(os.path.exists(os.path.join(self.root, "p.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "Изображения")))

    def test_organize_conflict_suffix(self):
        self.mk("p.jpg")
        self.mk("p.jpg", os.path.join(self.root, "Изображения"))
        plan = file_ops.plan_organize(self.root, "type", allowed_roots=self.roots)
        ops = [o for o in plan.ops if o.src.endswith("p.jpg") and os.path.dirname(o.src) == self.root]
        self.assertEqual(os.path.basename(ops[0].dst), "p (2).jpg")

    def test_organize_by_date(self):
        a = self.mk("a.txt")
        os.utime(a, (1700000000, 1700000000))
        plan = file_ops.plan_organize(self.root, "date", allowed_roots=self.roots)
        self.assertRegex(plan.ops[0].dst, r"20\d\d-\d\d")

    def test_apply_revalidates(self):
        a = self.mk("a.txt")
        plan = file_ops.plan_rename([a], pattern="a", replace="b", allowed_roots=self.roots)
        self.mk("b.txt")                                    # появился после планирования
        res = file_ops.apply(plan, self.log, allowed_roots=self.roots)
        self.assertEqual((res.done, len(res.failed)), (0, 1))
        self.assertTrue(os.path.exists(a))

    def test_journal_atomic_json(self):
        a = self.mk("a.txt")
        file_ops.apply(file_ops.plan_rename([a], pattern="a", replace="c", allowed_roots=self.roots), self.log,
                       allowed_roots=self.roots)
        import json
        with open(self.log, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["batches"]), 1)
        self.assertEqual([x for x in os.listdir(self.tmp) if ".tmp" in x], [])


if __name__ == "__main__":
    unittest.main()
