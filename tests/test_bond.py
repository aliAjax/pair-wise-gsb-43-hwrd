import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import DomainError, ProcurementService  # noqa: E402

CRITERIA = [
    {"name": "报价", "weight": 60, "kind": "cost", "max_value": 1000000},
    {"name": "质量", "weight": 40, "kind": "direct", "max_value": 100},
]


class BidBondTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ProcurementService(Path(self.tmp.name) / "test.db")
        self.vendor1 = self.service.create_vendor("proc1", "procurement", "V-101", "启明科技", "vendor1")
        self.vendor2 = self.service.create_vendor("proc1", "procurement", "V-102", "远山系统", "vendor2")
        self.vendor3 = self.service.create_vendor("proc1", "procurement", "V-103", "长河信创", "vendor3")

    def tearDown(self):
        self.tmp.cleanup()

    def create_tender(self, no, bond=50000, seconds=2):
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        tender = self.service.create_tender("proc1", "procurement", no, "保证金项目", deadline, CRITERIA, bond_amount=bond)
        return self.service.publish_tender("proc1", "procurement", tender["id"], tender["version"])

    def bid(self, tender, vendor, actor, price, quality):
        return self.service.submit_bid(
            actor, "vendor", tender["id"], vendor["id"], {"报价": price, "质量": quality}, price
        )

    def evaluate(self, bid_id, price, quality):
        self.service.evaluate_bid("eval1", "evaluator", bid_id, {"报价": price, "质量": quality})

    def open(self, tender):
        time.sleep(2.1)
        return self.service.open_bids("proc1", "procurement", tender["id"], tender["version"])

    def test_insufficient_bond_blocks_submission_and_topup_enables_it(self):
        tender = self.create_tender("T-BOND-1")
        bond = self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 30000, "bank")
        self.assertEqual(30000, bond["amount"])
        with self.assertRaises(DomainError) as ctx:
            self.bid(tender, self.vendor1, "vendor1", 800000, 90)
        self.assertEqual(402, ctx.exception.status)
        self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 20000, "alipay")
        bid = self.bid(tender, self.vendor1, "vendor1", 800000, 90)
        self.assertEqual("sealed", bid["status"])

    def test_refund_before_open_respects_sealed_bid_and_unlocks_after_withdraw(self):
        tender = self.create_tender("T-BOND-2")
        self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 60000, "bank")
        self.bid(tender, self.vendor1, "vendor1", 800000, 90)
        # 已投密封标：全额退回被拒（退回后不足额）
        with self.assertRaises(DomainError):
            self.service.refund_bond("vendor1", "vendor", tender["id"], self.vendor1["id"])
        # 退回后仍足额的部分可以退
        view = self.service.refund_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 10000)
        self.assertEqual(50000, view["amount"])
        # 撤标后可以全额退回
        current = self.service.get_tender("vendor1", "vendor", tender["id"])["bids"][0]
        self.service.withdraw_bid("vendor1", "vendor", current["id"], current["version"])
        view = self.service.refund_bond("vendor1", "vendor", tender["id"], self.vendor1["id"])
        self.assertEqual(0, view["amount"])
        self.assertEqual(60000, sum(tx["amount"] for tx in view["transactions"] if tx["kind"] == "pay"))

    def test_open_locks_bonds_disqualify_forfeits_and_award_settles(self):
        tender = self.create_tender("T-BOND-3")
        self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 50000, "bank")
        self.service.pay_bond("vendor2", "vendor", tender["id"], self.vendor2["id"], 50000, "alipay")
        self.service.pay_bond("vendor3", "vendor", tender["id"], self.vendor3["id"], 30000, "bank")
        self.service.pay_bond("vendor3", "vendor", tender["id"], self.vendor3["id"], 20000, "wechat")
        bid1 = self.bid(tender, self.vendor1, "vendor1", 800000, 90)
        bid2 = self.bid(tender, self.vendor2, "vendor2", 700000, 70)
        bid3 = self.bid(tender, self.vendor3, "vendor3", 750000, 80)

        opened = self.open(tender)
        self.assertEqual(3, len(opened["bids"]))
        opened_by_vendor = {b["vendor_id"]: b for b in opened["bids"]}
        bid2 = opened_by_vendor[self.vendor2["id"]]
        bid3 = opened_by_vendor[self.vendor3["id"]]
        bid1 = opened_by_vendor[self.vendor1["id"]]
        # 开标后锁定：参标供应商不能退、不能补
        with self.assertRaises(DomainError) as lock_ctx:
            self.service.refund_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 1)
        self.assertEqual(409, lock_ctx.exception.status)
        with self.assertRaises(DomainError):
            self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 1, "bank")

        # 废标：保证金立即没收
        self.service.disqualify_bid("proc1", "procurement", bid2["id"], "材料造假", bid2["version"])
        bonds = {b["vendor_id"]: b for b in self.service.get_tender("proc1", "supervisor", tender["id"])["bonds"]}
        self.assertEqual("forfeited", bonds[self.vendor2["id"]]["status"])
        self.assertEqual(0, bonds[self.vendor2["id"]]["amount"])
        self.assertTrue(bonds[self.vendor1["id"]]["locked"])

        # 授标结算：中标人转履约保证金，其余有效投标人按原渠道逐笔退回
        self.evaluate(bid1["id"], 800000, 90)
        self.evaluate(bid3["id"], 750000, 80)
        latest = self.service.get_tender("sup1", "supervisor", tender["id"])["tender"]
        award = self.service.award_tender("sup1", "supervisor", tender["id"], latest["version"])
        self.assertEqual(bid1["id"], award["award"]["winner"]["bid_id"])

        bonds = {b["vendor_id"]: b for b in self.service.get_tender("proc1", "supervisor", tender["id"])["bonds"]}
        winner_bond = bonds[self.vendor1["id"]]
        self.assertEqual("converted", winner_bond["status"])
        self.assertEqual(0, winner_bond["amount"])
        convert_tx = [tx for tx in winner_bond["transactions"] if tx["kind"] == "convert"]
        self.assertEqual(50000, convert_tx[0]["amount"])
        self.assertEqual("performance", convert_tx[0]["channel"])

        loser_bond = bonds[self.vendor3["id"]]
        self.assertEqual("refunded", loser_bond["status"])
        self.assertEqual(0, loser_bond["amount"])
        refund_tx = [tx for tx in loser_bond["transactions"] if tx["kind"] == "refund"]
        self.assertEqual({"bank", "wechat"}, {tx["channel"] for tx in refund_tx})
        pay_ids = {tx["id"] for tx in loser_bond["transactions"] if tx["kind"] == "pay"}
        self.assertTrue(pay_ids)
        self.assertEqual(pay_ids, {tx["source_payment_id"] for tx in refund_tx})

        # 被废标供应商的没收状态在授标后保持不变
        self.assertEqual("forfeited", bonds[self.vendor2["id"]]["status"])
        settlement = {s["vendor_id"]: s for s in award["award"]["bond_settlement"]}
        self.assertNotIn(self.vendor2["id"], settlement)
        self.assertEqual("converted", settlement[self.vendor1["id"]]["status"])
        self.assertEqual("refunded", settlement[self.vendor3["id"]]["status"])

    def test_non_bidder_bond_stays_refundable_after_open(self):
        tender = self.create_tender("T-BOND-4")
        self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 50000, "bank")
        self.bid(tender, self.vendor1, "vendor1", 800000, 90)
        self.service.pay_bond("vendor2", "vendor", tender["id"], self.vendor2["id"], 50000, "bank")
        self.open(tender)
        # 未参标供应商的保证金不锁定，开标后仍可退回
        view = self.service.refund_bond("vendor2", "vendor", tender["id"], self.vendor2["id"])
        self.assertEqual(0, view["amount"])
        with self.assertRaises(DomainError):
            self.service.refund_bond("vendor1", "vendor", tender["id"], self.vendor1["id"])

    def test_vendor_only_sees_own_bond(self):
        tender = self.create_tender("T-BOND-5")
        self.service.pay_bond("vendor1", "vendor", tender["id"], self.vendor1["id"], 50000, "bank")
        self.service.pay_bond("vendor2", "vendor", tender["id"], self.vendor2["id"], 50000, "alipay")
        vendor1_view = self.service.get_tender("vendor1", "vendor", tender["id"])["bonds"]
        self.assertEqual([self.vendor1["id"]], [b["vendor_id"] for b in vendor1_view])


if __name__ == "__main__":
    unittest.main()
