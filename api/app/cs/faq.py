"""内置 FAQ：常见问题直答层。

为什么不把 FAQ 放进 RAG 语料里一起检索：FAQ 是**确定性**知识——退换货几天、运费谁出，
这些答案是运营定的，不该被检索排序影响，更不该因为向量端点抽风就答不出来。所以 FAQ
走精确关键词匹配，排在检索之前。

代价是 FAQ 必须手工维护，且关键词要够准。太宽（"怎么""可以"）会把一切问题都吸走，
让拒答形同虚设；太窄则频繁落空。所以关键词一律取业务词，不用虚词。
"""

from __future__ import annotations

from dataclasses import dataclass

# 命中至少几个关键词才算"问的就是这条"。1 就够，因为关键词本身是业务词；
# 但这个 1 必须配"关键词足够专指"——见下方每条的 keywords 选取。
MIN_KEYWORD_HITS = 1

# 短于这个长度的关键词不参与匹配（"券""钱"这种单字误命中率太高）
MIN_KEYWORD_CHARS = 2


@dataclass(frozen=True)
class FaqItem:
    key: str
    category: str
    question: str
    answer: str
    keywords: tuple[str, ...]


FAQ_ITEMS: tuple[FaqItem, ...] = (
    FaqItem(
        key="shipping_time",
        category="发货",
        question="多久发货？",
        answer="现货商品在付款后 48 小时内发出，大促期间（如双十一）会延长到 72 小时；预售商品按详情页标注的发货日发出。",
        keywords=("多久发货", "什么时候发货", "几天发货", "发货时间", "什么时候发", "何时发货"),
    ),
    FaqItem(
        key="return_policy",
        category="售后",
        question="怎么退货 / 换货？",
        answer="支持 7 天无理由退换（食品类需未拆封）。在订单页点「申请退款」→ 选择原因 → 寄回后 48 小时内退款；质量问题由我们承担运费。",
        keywords=("退货", "退款", "换货", "退换", "不想要了", "七天无理由", "7天无理由"),
    ),
    FaqItem(
        key="shipping_fee",
        category="运费",
        question="包邮吗？运费多少？",
        answer="单笔满 49 元包邮，未满收 6 元运费；偏远地区（新疆、西藏）另计，下单时会显示实际运费。",
        keywords=("包邮", "运费", "邮费", "快递费", "满多少包邮"),
    ),
    FaqItem(
        key="coupon_usage",
        category="优惠",
        question="优惠券怎么用？",
        answer="在结算页「优惠券」一栏选择可用券，系统自动抵扣；一个订单只能用一张，优惠券不与其他满减叠加，过期不补。",
        keywords=("优惠券", "券怎么用", "用不了券", "抵扣", "满减", "优惠怎么用"),
    ),
    FaqItem(
        key="invoice",
        category="发票",
        question="能开发票吗？",
        answer="可以开电子普通发票，下单时在「发票信息」填写抬头与税号，发货后 24 小时内发到预留邮箱；专票需联系客服提供一般纳税人资质。",
        keywords=("发票", "开票", "税号", "报销"),
    ),
    FaqItem(
        key="shelf_life",
        category="商品",
        question="保质期多久？",
        answer="低糖燕麦饼干保质期 9 个月，生产日期印在盒底；开封后建议 15 天内吃完并密封防潮。",
        keywords=("保质期", "生产日期", "过期", "放多久", "新鲜"),
    ),
    FaqItem(
        key="address_change",
        category="订单",
        question="能改收货地址吗？",
        answer="未发货可以直接改：订单详情 → 修改地址。已发货只能联系快递改派，可能产生二次运费；生鲜类不支持改地址。",
        keywords=("改地址", "修改地址", "收货地址", "地址填错", "换个地址"),
    ),
    FaqItem(
        key="logistics",
        category="物流",
        question="怎么查物流？",
        answer="订单详情页有实时物流轨迹；超过 48 小时没有揽收记录，把订单号发我，我帮您催快递。",
        keywords=("物流", "快递单号", "到哪了", "查快递", "几天能到", "什么时候到"),
    ),
    FaqItem(
        key="payment",
        category="支付",
        question="支持哪些支付方式？",
        answer="支持微信、支付宝、抖音支付与银行卡；企业采购可对公转账，联系客服开具备货单。",
        keywords=("怎么付", "支付方式", "能刷卡", "对公", "花呗", "分期"),
    ),
    FaqItem(
        key="human_service",
        category="服务",
        question="怎么联系人工客服？",
        answer="我这边可以直接帮您转人工：描述一下问题，我建个工单，人工客服会在 30 分钟内接手。",
        keywords=("人工", "真人", "转客服", "客服电话", "找人"),
    ),
)


def match(question: str) -> tuple[FaqItem | None, int, list[str]]:
    """返回 (命中的 FAQ, 命中关键词数, 命中词列表)。

    多条命中时选"命中词更长"的那条：`退货` 比 `快递` 更专指，长词命中通常意味着
    问题更具体。同长则按声明顺序（先声明的优先级更高）。
    """
    text = (question or "").strip()
    if not text:
        return None, 0, []

    best: FaqItem | None = None
    best_hits: list[str] = []
    for item in FAQ_ITEMS:
        hits = [
            keyword
            for keyword in item.keywords
            if len(keyword) >= MIN_KEYWORD_CHARS and keyword in text
        ]
        if len(hits) < MIN_KEYWORD_HITS:
            continue
        if best is None or _weight(hits) > _weight(best_hits):
            best, best_hits = item, hits
    return best, len(best_hits), best_hits


def _weight(hits: list[str]) -> int:
    return sum(len(hit) for hit in hits)
