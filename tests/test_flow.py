import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import DomainError, ProcurementService  # noqa: E402


class ProcurementFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ProcurementService(Path(self.tmp.name) / "test.db")
        self.vendor1 = self.service.create_vendor("proc1", "procurement", "V-001", "启明科技", "vendor1")
        self.vendor2 = self.service.create_vendor("proc1", "procurement", "V-002", "远山系统", "vendor2")
        criteria = [
            {"name": "报价", "weight": 60, "kind": "cost", "max_value": 1000000},
            {"name": "质量", "weight": 40, "kind": "direct", "max_value": 100},
        ]
        self.tender = self.service.create_tender(
            "proc1", "procurement", "T-001", "数据中心设备", (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
            criteria, bond_required=50000,
        )
        self.tender = self.service.publish_tender("proc1", "procurement", self.tender["id"], self.tender["version"])

    def tearDown(self):
        self.tmp.cleanup()

    def fund(self, vendor, actor, amount=50000):
        return self.service.deposit_bond(actor, "vendor", self.tender["id"], vendor["id"], amount)

    def bid(self, vendor, actor, number, price, quality):
        self.fund(vendor, actor)
        return self.service.submit_bid(actor, "vendor", self.tender["id"], vendor["id"], {"报价": price, "质量": quality}, price)

    def test_complete_sealed_bid_open_evaluate_and_award_flow(self):
        self.bid(self.vendor1, "vendor1", "B1", 800000, 90)
        self.bid(self.vendor2, "vendor2", "B2", 700000, 80)
        before = self.service.get_tender("vendor1", "vendor", self.tender["id"])
        self.assertEqual("sealed", before["bids"][0]["status"])
        self.assertNotIn("payload", before["bids"][0])
        time.sleep(2.1)
        opened = self.service.open_bids("proc1", "procurement", self.tender["id"], self.tender["version"])
        self.assertEqual(2, len(opened["bids"]))
        self.service.evaluate_bid("eval1", "evaluator", opened["bids"][0]["id"], {"报价": 800000, "质量": 90})
        self.service.evaluate_bid("eval2", "evaluator", opened["bids"][0]["id"], {"报价": 800000, "质量": 90})
        self.service.evaluate_bid("eval1", "evaluator", opened["bids"][1]["id"], {"报价": 700000, "质量": 80})
        current = self.service.get_tender("sup1", "supervisor", self.tender["id"])
        self.assertEqual("opened", current["tender"]["status"])
        award = self.service.award_tender("sup1", "supervisor", self.tender["id"], current["tender"]["version"])
        self.assertEqual("awarded", award["tender"]["status"])
        self.assertEqual(opened["bids"][0]["id"], award["award"]["winner"]["bid_id"])

    def test_conflict_and_duplicate_evaluation_are_rejected(self):
        bid = self.bid(self.vendor1, "vendor1", "B3", 800000, 90)
        time.sleep(2.1)
        self.service.open_bids("proc1", "procurement", self.tender["id"], self.tender["version"])
        self.service.declare_conflict("eval1", "evaluator", self.tender["id"], "eval1", self.vendor1["id"], "曾受雇于供应商")
        with self.assertRaises(DomainError) as ctx:
            self.service.evaluate_bid("eval1", "evaluator", bid["id"], {"报价": 800000, "质量": 90})
        self.assertEqual(403, ctx.exception.status)
        self.service.evaluate_bid("eval2", "evaluator", bid["id"], {"报价": 800000, "质量": 90})
        with self.assertRaises(DomainError) as ctx2:
            self.service.evaluate_bid("eval2", "evaluator", bid["id"], {"报价": 800000, "质量": 90})
        self.assertEqual(409, ctx2.exception.status)

    def test_complaint_reevaluation_award_block_and_permissions(self):
        bid = self.bid(self.vendor1, "vendor1", "B4", 800000, 90)
        time.sleep(2.1)
        self.service.open_bids("proc1", "procurement", self.tender["id"], self.tender["version"])
        self.service.evaluate_bid("eval1", "evaluator", bid["id"], {"报价": 800000, "质量": 90})
        complaint = self.service.submit_complaint("vendor1", "vendor", self.tender["id"], "评分标准理解有误")
        current = self.service.get_tender("sup1", "supervisor", self.tender["id"])
        with self.assertRaises(DomainError):
            self.service.award_tender("sup1", "supervisor", self.tender["id"], current["tender"]["version"])
        resolved = self.service.resolve_complaint("sup1", "supervisor", complaint["id"], "accepted", "按新规则重评")
        self.assertEqual("accepted", resolved["status"])
        updated = self.service.get_tender("sup1", "supervisor", self.tender["id"])["tender"]
        self.assertEqual("reevaluation", updated["status"])
        self.assertEqual(2, updated["evaluation_round"])
        with self.assertRaises(DomainError) as ctx:
            self.service.open_bids("vendor1", "vendor", self.tender["id"], updated["version"])
        self.assertEqual(403, ctx.exception.status)


class BidBondTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ProcurementService(Path(self.tmp.name) / "test_bond.db")
        self.vendor1 = self.service.create_vendor("proc1", "procurement", "V-101", "启明科技", "v1")
        self.vendor2 = self.service.create_vendor("proc1", "procurement", "V-102", "远山系统", "v2")
        self.vendor3 = self.service.create_vendor("proc1", "procurement", "V-103", "云岚网络", "v3")
        criteria = [
            {"name": "报价", "weight": 60, "kind": "cost", "max_value": 1000000},
            {"name": "质量", "weight": 40, "kind": "direct", "max_value": 100},
        ]
        self.tender = self.service.create_tender(
            "proc1", "procurement", "T-BOND", "网络设备",
            (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
            criteria, bond_required=50000,
        )
        self.tender = self.service.publish_tender("proc1", "procurement", self.tender["id"], self.tender["version"])

    def tearDown(self):
        self.tmp.cleanup()

    def bid(self, vendor, actor, price, quality):
        return self.service.submit_bid(actor, "vendor", self.tender["id"], vendor["id"],
                                       {"报价": price, "质量": quality}, price)

    def test_insufficient_bond_blocks_submission_and_topup_allows_it(self):
        with self.assertRaises(DomainError) as ctx:
            self.bid(self.vendor1, "v1", 800000, 90)
        self.assertEqual(402, ctx.exception.status)
        bond = self.service.deposit_bond("v1", "vendor", self.tender["id"], self.vendor1["id"], 40000)
        self.assertEqual(40000, bond["paid_amount"])
        with self.assertRaises(DomainError):
            self.bid(self.vendor1, "v1", 800000, 90)
        bond = self.service.deposit_bond("v1", "vendor", self.tender["id"], self.vendor1["id"], 10000)
        self.assertEqual(50000, bond["paid_amount"])
        self.bid(self.vendor1, "v1", 800000, 90)
        # 有未撤回投标时不能退回
        with self.assertRaises(DomainError):
            self.service.refund_bond("v1", "vendor", self.tender["id"], self.vendor1["id"])

    def test_refund_before_open_and_lock_after_open(self):
        bond = self.service.deposit_bond("v2", "vendor", self.tender["id"], self.vendor2["id"], 50000)
        self.assertEqual("active", bond["status"])
        # 未投标的供应商开标前可申请退回（默认全额）
        bond = self.service.refund_bond("v2", "vendor", self.tender["id"], self.vendor2["id"])
        self.assertEqual(0, bond["paid_amount"])
        self.assertEqual("refund", bond["transactions"][-1]["tx_type"])
        # 重新缴纳、投标，开标后保证金锁死
        self.service.deposit_bond("v2", "vendor", self.tender["id"], self.vendor2["id"], 50000)
        self.bid(self.vendor2, "v2", 700000, 80)
        time.sleep(2.1)
        self.service.open_bids("proc1", "procurement", self.tender["id"], self.tender["version"])
        with self.assertRaises(DomainError) as deposit_ctx:
            self.service.deposit_bond("v2", "vendor", self.tender["id"], self.vendor2["id"], 1)
        self.assertEqual(409, deposit_ctx.exception.status)
        with self.assertRaises(DomainError) as refund_ctx:
            self.service.refund_bond("v2", "vendor", self.tender["id"], self.vendor2["id"])
        self.assertEqual(409, refund_ctx.exception.status)

    def test_award_settles_bonds_by_outcome(self):
        self.service.deposit_bond("v1", "vendor", self.tender["id"], self.vendor1["id"], 50000)
        self.service.deposit_bond("v2", "vendor", self.tender["id"], self.vendor2["id"], 50000)
        self.service.deposit_bond("v3", "vendor", self.tender["id"], self.vendor3["id"], 50000)
        bid1 = self.bid(self.vendor1, "v1", 800000, 90)
        bid2 = self.bid(self.vendor2, "v2", 700000, 80)
        bid3 = self.bid(self.vendor3, "v3", 750000, 85)
        time.sleep(2.1)
        opened = self.service.open_bids("proc1", "procurement", self.tender["id"], self.tender["version"])
        self.assertEqual(3, len(opened["bids"]))
        self.service.disqualify_bid("proc1", "procurement", bid2["id"], "资质不符", 2)
        self.service.evaluate_bid("eval1", "evaluator", bid1["id"], {"报价": 800000, "质量": 90})
        self.service.evaluate_bid("eval1", "evaluator", bid3["id"], {"报价": 750000, "质量": 85})
        current = self.service.get_tender("sup1", "supervisor", self.tender["id"])
        award = self.service.award_tender("sup1", "supervisor", self.tender["id"], current["tender"]["version"])
        outcomes = {item["vendor_id"]: item["outcome"] for item in award["bond_settlement"]}
        self.assertEqual("converted", outcomes[self.vendor1["id"]])  # 中标转履约保证金
        self.assertEqual("forfeited", outcomes[self.vendor2["id"]])  # 废标没收
        self.assertEqual("returned", outcomes[self.vendor3["id"]])   # 其余原路退还
        bonds = {row["vendor_id"]: row for row in
                 self.service.list_bonds("proc1", "procurement", self.tender["id"])["bonds"]}
        self.assertEqual("converted", bonds[self.vendor1["id"]]["status"])
        self.assertEqual(50000, bonds[self.vendor1["id"]]["settled_amount"])
        self.assertEqual("forfeited", bonds[self.vendor2["id"]]["status"])
        self.assertEqual("returned", bonds[self.vendor3["id"]]["status"])
        # 授标后一切已定，不能再动保证金
        with self.assertRaises(DomainError):
            self.service.deposit_bond("v3", "vendor", self.tender["id"], self.vendor3["id"], 1)
        with self.assertRaises(DomainError):
            self.service.refund_bond("v3", "vendor", self.tender["id"], self.vendor3["id"])
        self.assertIn("bond_settlement", award["award"])


if __name__ == "__main__":
    unittest.main()
