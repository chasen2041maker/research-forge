"""
============================================================
 附录 B:对抗式数据工厂(appendix/adversarial/red_blue.py)
============================================================

🎓 教学目标
    借鉴 GAN 思想:Blue 提方案 → Red 找漏洞 → Blue 修 → …
    每轮对话都是一条 DPO/SFT 训练数据。

📌 本教学版只实现一种对抗模式:Pairwise(Blue vs Red),单轮 + 一次裁决。
    更复杂的模式(多轮往返、Tournament Elo、Self-Play、Evolutionary)
    见技术方案 B.4 / 教学资料 9.8 列的 7 种模式,作为练手扩展。

💡 模型分工和"避免互相吹捧"
    - Blue:deepseek-chat,温度 0.6 → 创造性提方案/修方案
    - Red :deepseek-reasoner,温度 0.8 → 推理强 + 高温 → 找漏洞够狠
    - Judge:claude-opus-4-7 → 跨家族裁决,避免 DeepSeek 自己评自己手软
    这套搭配是技术方案 B.5 "避坑指南"明确推荐的:不同模型 + 不同 system + 不同温度。

📌 不进主 DAG 的原因
    对抗工厂产出的不是"研究结果",而是"训练样本"。挂在主图里会让每次研究都
    多花一笔对抗 LLM 账单,而且 DPO 数据的有效性需要离线评估。所以暴露成
    CLI(adversarial-run / adversarial-build),由用户显式触发。

------------------------------------------------------------
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.utils import logger

# Blue 的人设:正面建设。要"具体可执行"防止它甩出空洞口号被 Red 一枪秒掉。
BLUE_SYS = "你是 Blue Team,负责提出研究方案或对 Red Team 的批评做出修复。务必给出清晰、具体、可执行的方案。"

# Red 的人设:对抗式找漏洞。明确要 3 个最致命漏洞 + 攻击路径,
# 防止 LLM 出于"礼貌"只给一两个软批评。这种刻意设计的"角色不对称"
# 是对抗系统能产出有价值数据的关键。
RED_SYS = "你是 Red Team,职责是找到 Blue Team 方案的薄弱点和潜在失败模式。不要客气,列出 3 个最致命的漏洞,并说明攻击路径。"

# Judge 的输出 schema 写死在 system 里,确保 chat_json 拿到稳定结构。
# chosen 字段限定枚举值,方便 to_dpo_pair 直接做 if-else,不需要二次校验。
JUDGE_SYS = """\
你是对抗对话 Judge(Claude Opus)。基于一轮 Blue 原始方案 / Red 攻击 / Blue 修复,
打分并输出偏好对。返回 JSON:
{
  "blue_score": 1-10,
  "red_score": 1-10,
  "chosen": "blue_original|blue_fixed",  // 哪个版本更好
  "reason": "..."
}"""


@dataclass
class AdversarialRound:
    """一轮对抗的完整记录,既给 Judge 看也给数据工厂导出用。"""
    blue_original: str
    red_attack: str
    blue_fixed: str
    judgment: dict


@dataclass
class MultiRoundResult:
    """
    多轮对抗的完整产物。

    ▍对应设计文档 B.3:"Blue 提方案 → Red 找漏洞 → Blue 修 → Red 再攻 → …
       直到 Red 找不到漏洞,或达到最大轮数"。
    ▍每一轮都保留完整的 AdversarialRound,方便:
       - 转成多条 DPO 偏好对(到哪一轮都算数据)
       - 观察 proposal 的收敛过程
    """
    rounds: list[AdversarialRound] = field(default_factory=list)
    final_proposal: str = ""
    stopped_reason: str = ""  # "no_more_issue" | "max_rounds" | "red_failed"


def run_round(proposal: str) -> AdversarialRound:
    """
    跑一轮 Blue-Red-Judge 对抗。

    ▍执行顺序(为什么是 Red 先出手)
        Red 先看原始 proposal 找漏洞 → Blue 看着 Red 的攻击修方案 → Judge 比较两版。
        这样得到的 (blue_original, blue_fixed) 天然是一对"修复前 vs 修复后",
        可以直接转成 DPO 偏好对(to_dpo_pair)。

    ▍温度配置
        Red 高温(0.8):需要发散找各种攻击面;
        Blue 中温(0.6):修方案要新但又必须可执行;
        Judge 低温(0.3):裁决要稳定,同样输入给同样答案。

    ▍Judge 的降级
        Claude Opus 不可用(账户限流/网络问题)时,降级到 deepseek-reasoner。
        这是有意为之的 "best-effort":数据工厂的产出本身就是带噪的训练数据,
        宁可一条用降级 Judge,也不要整个 batch 因为 Claude 挂掉直接报废。
    """
    blue_chat = get_llm("chat")  # deepseek-chat,创造性
    red_chat = get_llm("reasoner")  # deepseek-reasoner,推理强
    judge_chat = get_llm("critical")  # claude-opus-4-7

    # Red 攻击
    red_resp = red_chat.chat(
        messages=[
            {"role": "system", "content": RED_SYS},
            {"role": "user", "content": f"Blue 方案:\n{proposal}"},
        ],
        purpose="adv_red",
        temperature=0.8,  # 高温增加对抗性
    )
    red_attack = red_resp.get("content", "")

    # Blue 修
    blue_resp = blue_chat.chat(
        messages=[
            {"role": "system", "content": BLUE_SYS},
            {"role": "user", "content": f"原方案:\n{proposal}\n\nRed 攻击:\n{red_attack}\n\n请修复漏洞。"},
        ],
        purpose="adv_blue_fix",
        temperature=0.6,
    )
    blue_fixed = blue_resp.get("content", "")

    # Judge 打分(失败降级到 reasoner,产数据期间不能因为 Claude 抖动整批失败)
    try:
        judgment = judge_chat.chat_json(
            messages=[
                {"role": "system", "content": JUDGE_SYS},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "blue_original": proposal,
                            "red_attack": red_attack,
                            "blue_fixed": blue_fixed,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            purpose="adv_judge",
            temperature=0.3,
        )
    except Exception as e:
        logger.warning("[adv] Judge 失败,降级 reasoner: {}", e)
        judgment = get_llm("reasoner").chat_json(
            messages=[
                {"role": "system", "content": JUDGE_SYS},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "blue_original": proposal,
                            "red_attack": red_attack,
                            "blue_fixed": blue_fixed,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            purpose="adv_judge_fallback",
        )

    return AdversarialRound(
        blue_original=proposal,
        red_attack=red_attack,
        blue_fixed=blue_fixed,
        judgment=judgment,
    )


def _red_found_issue(attack_text: str) -> bool:
    """
    粗判 Red 是否还找到了新漏洞。

    ▍判断规则(按成本排序)
        1. 空字符串 / 只有空白 → Red 失败,视作"没找到"
        2. 命中"未发现/没有问题/no issue/no more"等关键词 → 视作"没找到"
        3. 其他 → 视作找到了漏洞(保守策略,宁可多跑一轮)

    ▍为什么不上 LLM 做这个判断
        又调一次 LLM 既花钱又不稳定。关键词判定足够 MVP 用,
        生产版可以把这函数换成一个结构化字段(让 Red 返回 {found: bool, ...})。
    """
    text = (attack_text or "").strip().lower()
    if not text:
        return False
    stop_phrases = [
        "no more issue",
        "no more issues",
        "no issue found",
        "未发现",
        "没有发现",
        "没有问题",
        "没有漏洞",
        "暂无漏洞",
        "无需修复",
    ]
    return not any(p in text for p in stop_phrases)


def run_multi_round(proposal: str, max_rounds: int = 3) -> MultiRoundResult:
    """
    多轮 Red/Blue 对抗。

    ▍收敛条件(按优先级)
        1. Red 找不到新漏洞     → stopped_reason="no_more_issue"
        2. 达到 max_rounds 上限 → stopped_reason="max_rounds"
        3. 中途 Red 调用整体失败 → stopped_reason="red_failed"

    ▍max_rounds 默认 3
        - 1 轮就是 Pairwise 单次;
        - 3 轮基本够让方案暴露结构性弱点;
        - 再多轮 LLM 会开始重复自己,边际收益递减。
        用户想跑更多(做数据工厂)时,通过 CLI 传参覆盖。

    ▍和 run_round 的关系
        run_multi_round 直接复用 run_round 的三段式实现,只在外层做"继续/停"的判断。
        这样 Pairwise 单轮的行为和原来一致,不产生回归。
    """
    result = MultiRoundResult()
    current = proposal

    for i in range(max_rounds):
        try:
            r = run_round(current)
        except Exception as e:
            logger.warning("[adv] 多轮第 {} 轮 run_round 失败: {}", i + 1, e)
            result.stopped_reason = "red_failed"
            break

        result.rounds.append(r)
        current = r.blue_fixed or current

        if not _red_found_issue(r.red_attack):
            result.stopped_reason = "no_more_issue"
            logger.info("[adv] 第 {} 轮 Red 未发现新漏洞,停止", i + 1)
            break
    else:
        # for 正常跑完没 break
        result.stopped_reason = "max_rounds"

    result.final_proposal = current
    return result


def to_dpo_pair(round_: AdversarialRound) -> dict:
    """
    把一轮对抗压平成 DPO 训练对。

    ▍为什么要单独函数
        run_round 负责"对抗对话怎么进行",to_dpo_pair 负责"怎么把它变成训练样本"。
        分开后:
          - 上游对抗逻辑可以保持完整(完整保留三段对话,便于人工抽检);
          - 下游训练格式可以独立演进(以后想换 RLHF 格式只改这一处)。

    ▍chosen 字段语义
        chosen=blue_fixed → Red 的攻击是有效的(修复版更好,作为训练目标)
        chosen=blue_original → Red 的攻击没用(原版反而更好,Red 的弱攻击作为反面)
        两种情况都构成有效的 DPO 偏好对。
    """
    chosen_field = round_.judgment.get("chosen", "blue_fixed")
    chosen = round_.blue_fixed if chosen_field == "blue_fixed" else round_.blue_original
    rejected = round_.blue_original if chosen_field == "blue_fixed" else round_.blue_fixed
    return {
        "prompt": f"请基于以下 Red 攻击修复方案:\n{round_.red_attack}",
        "chosen": chosen,
        "rejected": rejected,
        "score": round_.judgment.get("blue_score", 0),
    }


def run_factory(
    seed_proposals: list[str],
    output_path: Path | None = None,
    *,
    multi_round: bool = False,
    max_rounds: int = 3,
) -> Path:
    """
    跑整个数据工厂:批量种子 → 对抗 → DPO 训练集。

    ▍设计要点
        - 单条失败不影响全局:try/except 包住每条,失败的种子记日志、跳过,
          其余继续。批量产数据时网络抖动/Judge 偶发 JSON 解析失败是常态。
        - 输出 jsonl(一行一对)而不是 json 数组:训练框架(TRL/DeepSpeed)
          天然按行流式读取,不用一次性把整个数据集读进内存。
        - 默认输出到 settings.OUTPUT_DIR/dpo_dataset.jsonl,允许覆盖
          (CI 跑测试想隔离时方便)。
    """
    out = output_path or settings.OUTPUT_DIR / "dpo_dataset.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as f:
        for i, prop in enumerate(seed_proposals):
            try:
                if multi_round:
                    # 多轮模式:每轮都写一条 DPO 对,单个种子可能产出多条
                    mr = run_multi_round(prop, max_rounds=max_rounds)
                    for r in mr.rounds:
                        pair = to_dpo_pair(r)
                        f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                    logger.info(
                        "[adv] 完成 {}/{} (多轮:{} rounds, 停止原因={})",
                        i + 1, len(seed_proposals), len(mr.rounds), mr.stopped_reason,
                    )
                else:
                    r = run_round(prop)
                    pair = to_dpo_pair(r)
                    f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                    logger.info("[adv] 完成 {}/{}", i + 1, len(seed_proposals))
            except Exception as e:
                logger.warning("[adv] 种子 {} 失败: {}", i, e)
    return out
