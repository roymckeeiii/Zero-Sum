"""rulesync.sync: copies are created, updated and deleted only where ticked; a pre-existing rule is backed up before
it is adopted; Gmail (labels) is called before the transaction on Inbox Zero's tables, never inside it."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import rulesync  # noqa: E402
from fakes import Accounts, Db, Patched  # noqa: E402

WRITES = ('INSERT INTO "Rule"', 'UPDATE "Rule"', 'DELETE FROM "Rule"', 'INSERT INTO "Action"', 'DELETE FROM "Action"',
          "INSERT INTO unified.rule_map", "DELETE FROM unified.rule_map", "INSERT INTO unified.rule_backup")


def rule(rid, name, **over):
    r = {f: None for f in rulesync.RULE_FIELDS} | {"name": name, "enabled": True, "automate": True, "instructions": "do it"}
    r.update(over)
    return (rid, *[r[f] for f in rulesync.RULE_FIELDS])


def action(rule_id, type_="LABEL", label="Receipts", label_id="L_master"):
    a = {f: None for f in rulesync.ACTION_FIELDS} | {"type": type_, "label": label}
    return (rule_id, *[a[f] for f in rulesync.ACTION_FIELDS], label_id)


class World(unittest.TestCase):
    def setUp(self):
        self.db, self.acc = Db(), Accounts("master@x.com", "two@x.com", "three@x.com")
        self.rules = {"acc1": [rule("m1", "Receipts")], "acc2": [], "acc3": []}
        self.actions = {"m1": [action("m1")]}
        self.map, self.include, self.events = [], {("m1", "acc2")}, []
        self.db.on('FROM "Rule"', lambda p: self.rules.get(p[0], []))
        self.db.on('FROM "Action"', lambda p: [a for rid in p[0] for a in self.actions.get(rid, [])])
        self.db.on("FROM unified.rule_map WHERE account_id", lambda p: [(m, t, h) for m, a, t, h in self.map if a == p[0]])
        self.db.on("FROM unified.rule_include", lambda p: sorted(self.include))

        def label_id(acc, name, create=True):
            self.events.append(("gmail", acc, name, self.db.open))
            return f"L_{acc}_{name}"
        self.p = Patched(rulesync, db=self.db, ACC=self.acc, log=lambda *a, **k: None, label_id=label_id,
                         labels=lambda acc: {"L_master": "Receipts"})
        self.p.__enter__()
        self.addCleanup(self.p.__exit__)

    def writes(self):
        return [(s, p) for s, p in self.db.sql if any(w in s for w in WRITES)]

    def ops(self, res):
        return [(o["op"], o["account"], o["rule"]) for o in res["ops"]]

    def digest(self):
        rules = rulesync.load_rules(self.db, "acc1")
        r = rules["m1"]
        return rulesync.digest(r, rulesync.master_actions("acc1", r))


class Sync(World):
    def test_a_ticked_rule_is_created_in_that_account_only(self):
        res = rulesync.sync()
        self.assertEqual(self.ops(res), [("create", "two@x.com", "Receipts")])
        ins = self.db.said('INSERT INTO "Rule"')
        self.assertEqual(len(ins), 1)
        target = ins[0][1][0]
        self.assertEqual(ins[0][1][1], "acc2")
        act = self.db.said('INSERT INTO "Action"')[0][1]
        self.assertEqual((act[1], act[2]), (target, "L_acc2_Receipts"))          # this account's own label id
        self.assertEqual(self.db.said("INSERT INTO unified.rule_map")[0][1][:3], ("m1", "acc2", target))
        self.assertEqual(self.db.commits, 1)
        self.assertFalse(self.db.said("unified.rule_backup (account_id"))        # nothing existed: nothing to back up

    def test_labels_are_resolved_before_the_transaction_never_inside_it(self):
        order = self.events
        real = self.db.execute

        def execute(sql, params=None):
            if any(w in sql for w in WRITES):
                order.append(("write", sql[:20]))
            return real(sql, params)
        self.db.execute = execute
        rulesync.sync()
        kinds = [e[0] for e in order]
        self.assertEqual(kinds[0], "gmail")
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "gmail"), kinds.index("write"))
        self.assertEqual([e[3] for e in order if e[0] == "gmail"], [0])           # no `with db()` block was open

    def test_a_label_that_cannot_be_resolved_writes_nothing(self):
        def boom(acc, name, create=True):
            raise RuntimeError("Gmail POST /labels: 500 backend")
        with Patched(rulesync, label_id=boom):
            res = rulesync.sync()
        self.assertEqual([o["op"] for o in res["ops"]], ["error"])
        self.assertIn("500", res["ops"][0]["detail"])
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.db.commits, 0)

    def test_preview_plans_without_touching_gmail_or_the_database(self):
        res = rulesync.sync(apply=False)
        self.assertEqual((self.ops(res), res["applied"]), ([("create", "two@x.com", "Receipts")], False))
        self.assertEqual((self.writes(), self.events, self.db.commits), ([], [], 0))

    def test_an_unchanged_copy_is_left_alone_and_a_changed_one_is_updated_without_backup(self):
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map = [("m1", "acc2", "t1", self.digest())]
        self.assertEqual(rulesync.sync()["ops"], [])
        self.assertEqual(self.writes(), [])
        self.rules["acc1"] = [rule("m1", "Receipts", instructions="now different")]
        self.assertEqual(self.ops(rulesync.sync()), [("update", "two@x.com", "Receipts")])
        upd = self.db.said('UPDATE "Rule"')[0]
        self.assertEqual(upd[1][-2:], ["t1", "acc2"])
        self.assertFalse(self.db.said("INSERT INTO unified.rule_backup"))        # its own copy: no backup needed

    def test_adopting_a_pre_existing_rule_saves_it_first(self):
        self.rules["acc2"] = [rule("old1", "Receipts", instructions="hand-written before the sync")]
        res = rulesync.sync()
        self.assertEqual(self.ops(res), [("adopt", "two@x.com", "Receipts")])
        w = [s for s, _ in self.writes()]
        self.assertIn("INSERT INTO unified.rule_backup", w[0])                   # before anything is overwritten
        self.assertIn('UPDATE "Rule"', w[1])
        sql, params = self.db.said("INSERT INTO unified.rule_backup")[0]
        self.assertIn("to_jsonb(r)", sql)                                        # the whole row, every column
        self.assertIn('FROM "Action" a', sql)                                    # and its actions
        self.assertEqual(params[1:], ("old1", "acc2"))
        self.assertFalse(self.db.said('INSERT INTO "Rule"'))

    def test_a_system_rule_is_adopted_by_type_before_name(self):
        self.rules["acc1"] = [rule("m1", "Newsletters", systemType="NEWSLETTER")]
        self.rules["acc2"] = [rule("byname", "Something else"), rule("bytype", "My news", systemType="NEWSLETTER")]
        rulesync.sync()
        self.assertEqual(self.db.said("INSERT INTO unified.rule_backup")[0][1][1], "bytype")

    def test_a_name_clash_is_reported_and_nothing_is_overwritten(self):
        self.rules["acc1"] = [rule("m1", "News", systemType="NEWSLETTER")]
        self.rules["acc2"] = [rule("a", "Other", systemType="NEWSLETTER"), rule("b", "News")]
        res = rulesync.sync()
        self.assertEqual([o["op"] for o in res["ops"]], ["error"])
        self.assertEqual(self.writes(), [])

    def test_unticking_deletes_only_the_copy_it_made(self):
        self.rules["acc2"] = [rule("t1", "Receipts"), rule("own", "My own rule")]
        self.map = [("m1", "acc2", "t1", "h")]
        self.include = set()
        self.assertEqual(self.ops(rulesync.sync()), [("delete", "two@x.com", "Receipts")])
        self.assertEqual([p for _, p in self.db.said('DELETE FROM "Rule"')], [("t1", "acc2")])

    def test_a_copy_whose_master_rule_is_gone_is_deleted(self):
        self.rules["acc1"] = []
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map = [("m1", "acc2", "t1", "h")]
        res = rulesync.sync()
        self.assertEqual((self.ops(res), res["ops"][0]["detail"]), ([("delete", "two@x.com", "Receipts")], "master rule removed"))

    def test_the_master_account_is_never_a_target_and_one_failing_account_does_not_stop_the_next(self):
        self.include = {("m1", "acc1"), ("m1", "acc2"), ("m1", "acc3")}
        real = self.db.handlers[:]

        def rules(p):
            if p[0] == "acc2":
                raise RuntimeError("acc2 is broken")
            return self.rules.get(p[0], [])
        self.db.on('FROM "Rule"', rules)
        res = rulesync.sync()
        self.assertEqual(self.ops(res), [("error", "two@x.com", ""), ("create", "three@x.com", "Receipts")])
        self.assertEqual([p[1] for _, p in self.db.said('INSERT INTO "Rule"')], ["acc3"])
        self.db.handlers = real

    def test_without_a_master_account_it_refuses(self):
        with Patched(rulesync, ACC=type("A", (), {"master": staticmethod(lambda: None)})()):
            with self.assertRaises(PermissionError):
                rulesync.sync()


if __name__ == "__main__":
    unittest.main()
