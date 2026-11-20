"""rulesync.sync: copies are created, updated and deleted only where ticked; a pre-existing rule is backed up before
it is adopted, and put back (never deleted) when it is unticked; deletes need the user's confirmation, or are capped in
the automatic sync, and wait when MASTER_EMAIL changed; Gmail (labels) is called before the transaction on Inbox
Zero's tables, never inside it."""
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
        self.backup, self.settings = [], {}   # unified.rule_backup rows (id, account, rule id, reason, rule, actions)
        self.db.on('FROM "Rule"', lambda p: self.rules.get(p[0], []))
        self.db.on('FROM "Action"', lambda p: [a for rid in p[0] for a in self.actions.get(rid, [])])
        # rule_map rows: (master rule, account, target, hash[, adopted])
        self.db.on("FROM unified.rule_map WHERE account_id", lambda p: [
            (e[0], e[2], e[3], e[4] if len(e) > 4 else False) for e in self.map if e[1] == p[0]])
        self.db.on("FROM unified.rule_backup WHERE account_id", lambda p: [
            (b[0], b[2], b[4], b[5]) for b in sorted(self.backup, reverse=True)
            if b[1] == p[0] and b[3].startswith("adopted")])
        self.db.on("FROM unified.settings", lambda p: [(self.settings[p[0]],)] if p[0] in self.settings else [])
        self.db.on("FROM unified.rule_include", lambda p: sorted(self.include))
        # (master rule, account) where forward / send / reply / webhook may be copied: OK'd for the destinations the
        # master rule has now, unless ok_keys names the destinations the OK was given for
        self.outbound, self.ok_keys = set(), {}
        self.db.on("FROM unified.rule_include WHERE outbound", lambda p: [
            (m, a, self.ok_keys.get((m, a), self.dest_key(m))) for m, a in sorted(self.outbound)])

        def label_id(acc, name, create=True):
            self.events.append(("gmail", acc, name, self.db.open))
            return f"L_{acc}_{name}"
        self.p = Patched(rulesync, db=self.db, ACC=self.acc, log=lambda *a, **k: None, label_id=label_id,
                         labels=lambda acc: {"L_master": "Receipts"})
        self.p.__enter__()
        self.addCleanup(self.p.__exit__)

    def dest_key(self, mid):
        return rulesync.dest_key([dict(zip(rulesync.ACTION_FIELDS, t[1:-1])) for t in self.actions.get(mid, [])])

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

    def test_a_master_rule_renamed_onto_another_rule_there_is_reported_and_the_rest_still_sync(self):
        self.rules["acc1"] = [rule("m1", "Bills"), rule("m2", "Travel")]
        self.actions = {}
        self.rules["acc2"] = [rule("t1", "Receipts"), rule("own", "Bills")]      # t1 was the copy of m1, as "Receipts"
        self.map = [("m1", "acc2", "t1", "old")]
        self.include = {("m1", "acc2"), ("m2", "acc2")}
        res = rulesync.sync()
        self.assertEqual(self.ops(res), [("error", "two@x.com", "Bills"), ("create", "two@x.com", "Travel")])
        self.assertIn("name", res["ops"][0]["detail"])
        self.assertFalse(self.db.said('UPDATE "Rule"'))                          # neither t1 nor "own" is touched
        self.assertEqual([p[1][0] for p in self.db.said("INSERT INTO unified.rule_map")], ["m2"])

    def test_a_system_type_already_held_by_another_rule_is_reported(self):
        self.rules["acc1"] = [rule("m1", "Receipts", systemType="RECEIPT")]
        self.rules["acc2"] = [rule("t1", "Receipts"), rule("own", "Mine", systemType="RECEIPT")]
        self.map = [("m1", "acc2", "t1", "old")]
        self.include = {("m1", "acc2")}
        res = rulesync.sync()
        self.assertEqual([o["op"] for o in res["ops"]], ["error"])
        self.assertIn("system type", res["ops"][0]["detail"])
        self.assertEqual(self.writes(), [])

    def test_unticking_deletes_only_the_copy_it_made(self):
        self.rules["acc2"] = [rule("t1", "Receipts"), rule("own", "My own rule")]
        self.map = [("m1", "acc2", "t1", "h")]
        self.include = set()
        self.assertEqual(self.ops(rulesync.sync(confirm=["acc2:t1"])), [("delete", "two@x.com", "Receipts")])
        self.assertEqual([p for _, p in self.db.said('DELETE FROM "Rule"')], [("t1", "acc2")])

    def test_a_copy_whose_master_rule_is_gone_is_deleted(self):
        self.rules["acc1"] = []
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map = [("m1", "acc2", "t1", "h")]
        res = rulesync.sync(confirm=["acc2:t1"])
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


class Removals(World):
    """Unticking never deletes a rule the user had written, and deletes of copies need a confirmation."""

    def setUp(self):
        super().setUp()
        self.logged = []
        self.p2 = Patched(rulesync, log=lambda kind, msg, acc=None: self.logged.append((kind, msg, acc)))
        self.p2.__enter__()
        self.addCleanup(self.p2.__exit__)

    def test_a_preview_lists_each_delete_with_the_key_to_confirm_it(self):
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map, self.include = [("m1", "acc2", "t1", "h")], set()
        (o,) = rulesync.sync(apply=False)["ops"]
        self.assertEqual((o["op"], o["key"]), ("delete", "acc2:t1"))
        self.assertEqual(self.writes(), [])

    def test_sync_now_without_confirmation_deletes_nothing_and_says_it_waits(self):
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map, self.include = [("m1", "acc2", "t1", "h")], set()
        res = rulesync.sync()
        self.assertEqual(self.ops(res), [("held", "two@x.com", "Receipts")])
        self.assertEqual(self.writes(), [])
        self.assertEqual([k for k, _, _ in self.logged], ["rules-pending"])

    def test_a_confirmation_for_another_rule_does_not_delete_this_one(self):
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map, self.include = [("m1", "acc2", "t1", "h")], set()
        rulesync.sync(confirm=["acc2:other", "acc3:t1"])
        self.assertFalse(self.db.said('DELETE FROM "Rule"'))

    def test_an_adopted_rule_is_put_back_as_it_was_never_deleted(self):
        own = {"id": "old1", "name": "My receipts", "instructions": "hand-written", "enabled": True, "automate": False,
               "systemType": None, "emailAccountId": "acc2"}
        acts = [{"type": "ARCHIVE", "label": None, "labelId": None}, {"type": "LABEL", "label": "Mine", "labelId": "L_own"}]
        self.backup = [(7, "acc2", "old1", "adopted as a copy of master rule Receipts", own, acts)]
        self.rules["acc2"] = [rule("old1", "Receipts")]
        self.map, self.include = [("m1", "acc2", "old1", "h", True)], set()
        res = rulesync.sync(confirm=["acc2:old1"])
        self.assertEqual(self.ops(res), [("restore", "two@x.com", "Receipts")])
        self.assertFalse(self.db.said('DELETE FROM "Rule"'))
        upd = self.db.said('UPDATE "Rule"')[0][1]
        self.assertEqual(upd[:3], ["My receipts", "hand-written", False])     # the user's own values
        self.assertEqual(upd[-2:], ["old1", "acc2"])
        self.assertEqual([p[2] for _, p in self.db.said('INSERT INTO "Action"')], [None, "L_own"])  # its own label id
        self.assertIn("INSERT INTO unified.rule_backup", [s for s, _ in self.writes()][0])  # the copy is saved first
        self.assertEqual(self.db.said("DELETE FROM unified.rule_map")[0][1], ("m1", "acc2"))

    def test_a_mapping_made_before_the_adopted_flag_is_known_by_its_backup(self):
        own = {"name": "Mine", "enabled": True, "automate": True}
        self.backup = [(3, "acc2", "old1", "adopted as a copy of master rule Receipts", own, [])]
        self.rules["acc1"] = []
        self.rules["acc2"] = [rule("old1", "Receipts")]
        self.map = [("m1", "acc2", "old1", "h")]                                   # adopted = false (old row)
        self.assertEqual(self.ops(rulesync.sync(confirm=["acc2:old1"])), [("restore", "two@x.com", "Receipts")])
        self.assertFalse(self.db.said('DELETE FROM "Rule"'))

    def test_an_adopted_rule_without_a_backup_is_detached_and_kept(self):
        self.rules["acc2"] = [rule("old1", "Receipts")]
        self.map, self.include = [("m1", "acc2", "old1", "h", True)], set()
        self.assertEqual(self.ops(rulesync.sync(confirm=["acc2:old1"])), [("detach", "two@x.com", "Receipts")])
        self.assertFalse(self.db.said('DELETE FROM "Rule"'))
        self.assertFalse(self.db.said('UPDATE "Rule"'))
        self.assertEqual(self.db.said("DELETE FROM unified.rule_map")[0][1], ("m1", "acc2"))

    def test_adopting_records_the_mapping_as_adopted(self):
        self.rules["acc2"] = [rule("old1", "Receipts")]
        rulesync.sync()
        self.assertIs(self.db.said("INSERT INTO unified.rule_map")[0][1][-1], True)

    def test_the_automatic_sync_deletes_at_most_the_cap_and_holds_the_rest(self):
        n = rulesync.AUTO_MAX_DELETES + 2
        self.rules["acc1"] = []
        self.rules["acc2"] = [rule(f"t{i}", f"R{i}") for i in range(n)]
        self.map = [(f"m{i}", "acc2", f"t{i}", "h") for i in range(n)]
        res = rulesync.sync(auto=True)
        self.assertEqual(rulesync.AUTO_MAX_DELETES, 3)
        self.assertEqual(len(self.db.said('DELETE FROM "Rule"')), 3)
        self.assertEqual([o["op"] for o in res["ops"]].count("held"), 2)
        self.assertEqual([k for k, _, _ in self.logged].count("rules-pending"), 2)

    def test_a_master_change_holds_every_removal_until_sync_now_confirms_it(self):
        self.settings = {"rules_master": "old-master@x.com"}
        self.rules["acc1"] = []
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map = [("m1", "acc2", "t1", "h")]
        for kw in ({"auto": True}, {"confirm": ["acc2:t1"]}, {"auto": True, "confirm": ["acc2:t1", "master-change"]}):
            res = rulesync.sync(**kw)
            self.assertEqual([o["op"] for o in res["ops"]], ["held"], kw)
            self.assertEqual(res["masterChanged"]["from"], "old-master@x.com")
            self.assertFalse(self.db.said('DELETE FROM "Rule"'), kw)
            self.assertFalse(self.db.said("INSERT INTO unified.settings"), kw)
        res = rulesync.sync(confirm=["acc2:t1", "master-change"])
        self.assertEqual([o["op"] for o in res["ops"]], ["delete"])
        self.assertEqual(self.db.said("INSERT INTO unified.settings")[0][1], ("rules_master", "master@x.com"))

    def test_the_first_sync_records_the_master(self):
        rulesync.sync()
        self.assertEqual(self.db.said("INSERT INTO unified.settings")[0][1], ("rules_master", "master@x.com"))

    def test_backups_of_rules_still_mapped_are_never_pruned(self):
        self.rules["acc2"] = [rule("old1", "Receipts")]
        rulesync.sync()
        (sql, _), = self.db.said("DELETE FROM unified.rule_backup")
        self.assertIn("LIMIT 500", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM unified.rule_map", sql)


class Restore(World):
    """Rules tab: any backup can be put back by hand."""

    def setUp(self):
        super().setUp()
        self.row = None
        self.db.on("FROM unified.rule_backup WHERE id", lambda p: [self.row] if self.row else [])
        self.db.on("RETURNING master_rule_id", lambda p: [("m1",)])

    def test_a_backup_is_written_back_over_the_copy_and_sync_lets_go_of_it(self):
        self.rules["acc2"] = [rule("old1", "Receipts")]
        self.row = ("acc2", "old1", {"name": "Mine", "enabled": True, "automate": True}, [{"type": "ARCHIVE"}])
        res = rulesync.restore({"id": "5"})
        self.assertEqual((res["rule"], res["account"]), ("Mine", "two@x.com"))
        w = [s for s, _ in self.writes()]
        self.assertIn("INSERT INTO unified.rule_backup", w[0])                    # what is there now is kept too
        self.assertEqual(self.db.said('UPDATE "Rule"')[0][1][0], "Mine")
        self.assertEqual(self.db.said("DELETE FROM unified.rule_include")[0][1], ("m1", "acc2"))  # untick: no re-adopt
        self.assertEqual(self.db.commits, 1)

    def test_a_deleted_rule_is_created_again_with_its_own_id(self):
        self.row = ("acc2", "gone1", {"name": "Mine", "enabled": True}, [])
        rulesync.restore({"id": 5})
        self.assertEqual(self.db.said('INSERT INTO "Rule"')[0][1][:2], ["gone1", "acc2"])
        self.assertFalse(self.db.said("INSERT INTO unified.rule_backup"))

    def test_a_backup_of_an_account_out_of_scope_or_the_master_is_refused(self):
        for acc in ("acc1", "acc9"):
            self.row = (acc, "r1", {"name": "Mine"}, [])
            with self.assertRaises(KeyError):
                rulesync.restore({"id": 5})
        self.assertEqual(self.writes(), [])

    def test_a_name_clash_is_refused_before_anything_is_written(self):
        self.rules["acc2"] = [rule("old1", "Receipts"), rule("x", "Mine")]
        self.row = ("acc2", "old1", {"name": "Mine", "enabled": True}, [])
        with self.assertRaises(ValueError):
            rulesync.restore({"id": 5})
        self.assertEqual(self.writes(), [])


class Outbound(World):
    """Forward / send / reply / webhook actions point at the master's destinations: they are shown, and copied only
    where that was allowed for the account."""

    def setUp(self):
        super().setUp()
        fwd = {f: None for f in rulesync.ACTION_FIELDS} | {"type": "FORWARD", "to": "accountant@example.com", "cc": "boss@example.com"}
        hook = {f: None for f in rulesync.ACTION_FIELDS} | {"type": "CALL_WEBHOOK", "url": "https://hooks.example.com/x"}
        self.actions = {"m1": [action("m1"), ("m1", *[fwd[f] for f in rulesync.ACTION_FIELDS], None),
                               ("m1", *[hook[f] for f in rulesync.ACTION_FIELDS], None)]}

    def test_a_rule_that_forwards_is_not_copied_without_permission(self):
        for res in (rulesync.sync(apply=False), rulesync.sync()):
            (o,) = res["ops"]
            self.assertEqual(o["op"], "outbound")
            self.assertIn("forward to accountant@example.com, cc boss@example.com", o["detail"])
            self.assertIn("webhook https://hooks.example.com/x", o["detail"])
        self.assertEqual(self.writes(), [])

    def test_an_existing_copy_is_not_updated_to_forward_without_permission(self):
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.map = [("m1", "acc2", "t1", "old")]
        self.assertEqual([o["op"] for o in rulesync.sync()["ops"]], ["outbound"])
        self.assertEqual(self.writes(), [])

    def test_once_allowed_it_is_copied_and_the_destinations_are_in_the_plan(self):
        self.outbound = {("m1", "acc2")}
        (o,) = rulesync.sync()["ops"]
        self.assertEqual(o["op"], "create")
        self.assertIn("forward to accountant@example.com", o["detail"])
        types = [p[-len(rulesync.ACTION_FIELDS)] for _, p in self.db.said('INSERT INTO "Action"')]
        self.assertEqual(types, ["LABEL", "FORWARD", "CALL_WEBHOOK"])

    def test_allowing_is_its_own_step_and_unticking_clears_it(self):
        rulesync.set_include({"rule": "m1", "account": "acc2", "include": True})
        self.assertNotIn("outbound", self.db.sql[-1][0])
        rulesync.set_include({"rule": "m1", "account": "acc2", "include": True, "outbound": True})
        self.assertIn("DO UPDATE SET outbound", self.db.sql[-1][0])
        self.assertEqual(self.db.sql[-1][1], ("m1", "acc2", True, self.dest_key("m1")))   # the OK is for these

    def copy_that_forwards(self, to="accountant@example.com"):
        fwd = {f: None for f in rulesync.ACTION_FIELDS} | {"type": "FORWARD", "to": to}
        self.rules["acc2"] = [rule("t1", "Receipts")]
        self.actions["t1"] = [action("t1", label_id="L_acc2"), ("t1", *[fwd[f] for f in rulesync.ACTION_FIELDS], None)]

    def test_taking_back_allow_sending_removes_the_sending_actions_from_the_copy(self):
        self.copy_that_forwards()
        self.map = [("m1", "acc2", "t1", self.digest())]
        for auto in (False, True):   # the automatic sync does it too: it only ever reduces sending
            self.db.sql.clear()
            (o,) = rulesync.sync(auto=auto)["ops"]
            self.assertEqual(o["op"], "stop-sending")
            self.assertIn("forward to accountant@example.com", o["detail"])
            w = [s for s, _ in self.writes()]
            self.assertIn("INSERT INTO unified.rule_backup", w[0])                # the copy as it was is saved first
            self.assertEqual(self.db.said("INSERT INTO unified.rule_backup")[0][1][1:], ("t1", "acc2"))
            types = [p[-len(rulesync.ACTION_FIELDS)] for _, p in self.db.said('INSERT INTO "Action"')]
            self.assertEqual(types, ["LABEL"])                                   # no FORWARD left
            self.assertEqual(self.db.said('INSERT INTO "Action"')[0][1][1:3], ["t1", "L_acc2"])   # its own label kept
            self.assertNotEqual(self.db.said("INSERT INTO unified.rule_map")[0][1][3], self.digest())

    def test_a_copy_made_before_the_ok_existed_stops_sending_too(self):
        self.copy_that_forwards()
        self.map = [("m1", "acc2", "t1", "made-before-outbound-was-checked")]
        self.assertEqual([o["op"] for o in rulesync.sync()["ops"]], ["stop-sending"])

    def test_an_ok_counts_only_for_the_destinations_it_was_given_for(self):
        self.copy_that_forwards()
        self.actions["m1"][1] = ("m1", *[({f: None for f in rulesync.ACTION_FIELDS} | {
            "type": "FORWARD", "to": "accountant@example.com"})[f] for f in rulesync.ACTION_FIELDS], None)
        self.actions["m1"].pop()
        old = self.dest_key("m1")
        self.map = [("m1", "acc2", "t1", self.digest())]
        self.outbound, self.ok_keys = {("m1", "acc2")}, {("m1", "acc2"): old}
        self.actions["m1"][1] = ("m1", *[({f: None for f in rulesync.ACTION_FIELDS} | {
            "type": "FORWARD", "to": "eve@example.com"})[f] for f in rulesync.ACTION_FIELDS], None)
        for auto in (True, False):
            (o,) = rulesync.sync(auto=auto)["ops"]
            self.assertEqual(o["op"], "outbound")                                # not copied: needs a new OK
            self.assertIn("eve@example.com", o["detail"])
            self.assertIn("allow it again", o["detail"])
        self.assertEqual(self.writes(), [])                                      # the copy still sends where OK'd
        self.ok_keys = {}                                                        # OK'd again, for eve
        self.assertEqual([o["op"] for o in rulesync.sync()["ops"]], ["update"])

    def test_an_ok_from_before_destinations_were_recorded_is_not_enough(self):
        self.outbound, self.ok_keys = {("m1", "acc2")}, {("m1", "acc2"): None}
        self.assertEqual([o["op"] for o in rulesync.sync()["ops"]], ["outbound"])
        self.assertEqual(self.writes(), [])

    def test_an_action_type_not_known_to_stay_inside_is_treated_as_sending(self):
        self.actions = {"m1": [action("m1"), action("m1", "NOTIFY_SENDER", None, None)]}
        (o,) = rulesync.sync()["ops"]
        self.assertEqual(o["op"], "outbound")
        self.assertIn("NOTIFY_SENDER", o["detail"])
        self.assertEqual(self.writes(), [])

    def test_allowing_is_refused_when_the_destinations_changed_since_the_page_showed_them(self):
        with self.assertRaises(ValueError):
            rulesync.set_include({"rule": "m1", "account": "acc2", "include": True, "outbound": True,
                                  "destinations": "what-the-page-showed-before"})
        self.assertFalse(self.db.said("INSERT INTO unified.rule_include"))
        rulesync.set_include({"rule": "m1", "account": "acc2", "include": True, "outbound": True,
                              "destinations": self.dest_key("m1")})
        self.assertEqual(self.db.said("INSERT INTO unified.rule_include")[0][1][2:], (True, self.dest_key("m1")))
        rulesync.set_include({"rule": "m1", "account": "acc2", "include": True, "outbound": False})
        self.assertEqual(self.db.said("INSERT INTO unified.rule_include")[1][1][2:], (False, None))

    def test_the_rules_tab_shows_where_each_rule_sends(self):
        self.outbound = {("m1", "acc3")}
        self.db.on("FROM unified.log", [])
        with Patched(rulesync, setting=lambda k, d=None: d):
            st = rulesync.state()
        (r,) = st["rules"]
        self.assertEqual(r["outbound"], ["forward to accountant@example.com, cc boss@example.com",
                                         "webhook https://hooks.example.com/x"])
        self.assertEqual(r["outboundAllowed"], {"acc2": False, "acc3": True})
        self.assertEqual(r["cells"]["acc2"], "not sending")
        self.assertEqual(r["outboundKey"], self.dest_key("m1"))
        self.ok_keys = {("m1", "acc3"): "older"}
        with Patched(rulesync, setting=lambda k, d=None: d):
            (r,) = rulesync.state()["rules"]
        self.assertEqual((r["outboundAllowed"]["acc3"], r["outboundStale"]["acc3"]), (False, True))


class InboxZeroV230(World):
    """Inbox Zero v2.30 adds Action.emailAccountId and messaging-channel actions; v2.25.8 has neither."""

    def column(self, present):
        hit = [(1,)] if present else []
        self.db.on("information_schema.columns", lambda p: hit if p == ("Action", "emailAccountId") else [])

    def action_insert(self):
        (sql, params), = self.db.said('INSERT INTO "Action"')
        cols = [c.strip().strip('"') for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        cols.remove("updatedAt")                                                   # now(), not a parameter
        self.assertEqual(sql.count("%s"), len(params))
        return dict(zip(cols, params))

    def test_v2_25_8_copies_actions_without_the_account_column(self):
        self.column(False)
        rulesync.sync()
        a = self.action_insert()
        self.assertNotIn("emailAccountId", a)
        self.assertEqual((a["labelId"], a["type"]), ("L_acc2_Receipts", "LABEL"))

    def test_v2_30_copies_carry_the_target_account_not_the_master(self):
        self.column(True)
        rulesync.sync()
        a = self.action_insert()
        target = self.db.said('INSERT INTO "Rule"')[0][1][0]
        self.assertEqual((a["ruleId"], a["emailAccountId"], a["labelId"]), (target, "acc2", "L_acc2_Receipts"))

    def test_the_column_is_checked_on_every_sync_so_an_upgrade_needs_no_restart(self):
        self.column(False)
        rulesync.sync()
        self.assertNotIn("emailAccountId", self.db.said('INSERT INTO "Action"')[0][0])
        self.db.sql.clear()
        self.map = []                                                              # make the copy again
        self.column(True)
        rulesync.sync()
        self.assertIn("emailAccountId", self.db.said('INSERT INTO "Action"')[0][0])
        checks = self.db.said("information_schema.columns")
        self.assertEqual(len(checks), 1)                                           # once per account pass
        self.assertIn("table_schema = 'public'", checks[0][0])

    def test_messaging_channel_actions_are_not_copied(self):
        self.actions = {"m1": [action("m1"), action("m1", "NOTIFY_MESSAGING_CHANNEL", None, None),
                               action("m1", "DRAFT_MESSAGING_CHANNEL", None, None), action("m1", "MARK_READ", None, None)]}
        rulesync.sync()
        self.assertEqual([p[-len(rulesync.ACTION_FIELDS)] for _, p in self.db.said('INSERT INTO "Action"')],
                         ["LABEL", "MARK_READ"])

    def test_a_rule_with_only_messaging_channel_actions_is_copied_with_none(self):
        self.actions = {"m1": [action("m1", "NOTIFY_MESSAGING_CHANNEL", None, None)]}
        self.assertEqual(self.ops(rulesync.sync()), [("create", "two@x.com", "Receipts")])
        self.assertEqual(self.db.said('INSERT INTO "Action"'), [])
        self.assertEqual(len(self.db.said('INSERT INTO "Rule"')), 1)


if __name__ == "__main__":
    unittest.main()
