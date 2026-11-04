"""Call-domain MT postedit regression."""
from __future__ import annotations

from app.mt_postedit import postedit_en_zh, postedit_zh_en


def test_en_zh_delay_not_procrastination():
    assert "延迟" in postedit_en_zh(
        "How much delay is there today?", "今天还有多少拖延?"
    )
    assert "拖延" not in postedit_en_zh(
        "how much delay is there today", "今天还有多少拖延?"
    )


def test_en_zh_interview_and_reply():
    assert "这一面" in postedit_en_zh(
        "I might not pass this interview round.", "我可能不会通过这次面试"
    )
    assert "消息" in postedit_en_zh(
        "Did you reply to my message?", "你回复我的信息了吗?"
    )


def test_en_zh_unrelated_untouched():
    zh = "你能再说一遍吗？"
    assert postedit_en_zh("Can you say that again?", zh) == zh


def test_en_zh_hear_rule_not_overbroad():
    """Regression: broad can't-hear must not clobber unrelated sentences."""
    raw = "我们听不到对方的声音"
    assert postedit_en_zh("We can't hear the other side at all.", raw) == raw
    raw2 = "如果客户在重新启动后仍然听不到我们的声音 我们的计划是什么?"
    assert (
        postedit_en_zh(
            "What's our plan if the client still can't hear us after the reboot?",
            raw2,
        )
        == raw2
    )
    assert "听不清你说什么" in postedit_en_zh(
        "I can't hear what you're saying clearly.", "我听不清你在说什么"
    )


def test_en_zh_domain_term_latency_echo_duplex():
    assert "延迟" in postedit_en_zh(
        "Latency is around two hundred millis.", "延缓时间大约200毫克左右"
    )
    assert "毫秒" in postedit_en_zh(
        "Latency is around two hundred millis.", "延缓时间大约200毫克左右"
    )
    assert "回声" in postedit_en_zh(
        "Is the echo gone on your side?", "反响在你这边吗?"
    )
    assert "双工" in postedit_en_zh(
        "Duplex still feels a bit choppy on my end.", "双面还是有点兴奋"
    )
    # must not break already-correct echo sentence
    ok = "你说话时有奇怪的回声"
    assert postedit_en_zh("There's a weird echo when you talk.", ok) == ok


def test_en_zh_term_literal_ship_build():
    assert "上线" in postedit_en_zh(
        "We're shipping Friday at 2 p.m.", "我们周五下午2点发货"
    )
    assert "坐船" not in postedit_en_zh(
        "I don't think we should ship tonight.", "我觉得今晚我们不该坐船"
    )
    assert "构建" in postedit_en_zh(
        "Hang on, I'm pulling up the build log.", "等等,我要拉起建筑日志"
    )
    assert "构建" in postedit_en_zh(
        "The build's been failing since Tuesday and nobody owns the fix yet.",
        "大楼从星期二开始就一直失灵 还没有人拥有修补设备",
    )


def test_en_zh_meeting_term_and_idiom():
    assert "截止" in postedit_en_zh(
        "What's the deadline for this?", "期限是多少?"
    )
    assert "幻灯片" in postedit_en_zh(
        "I'll ping you the deck after this.", "之后我再把甲板打给你"
    )
    assert "站会" in postedit_en_zh(
        "We need a quick sync before standup.", "我们需要快速同步 之前站起来。"
    )
    assert "三点" in postedit_en_zh(
        "Are we still on for three?", "我们还在谈3个吗?"
    )
    assert "再聊" in postedit_en_zh(
        "Let's circle back on that tomorrow.", "明天再回转"
    )
    assert "收束" in postedit_en_zh(
        "Let's wrap in five and send notes after.", "5分钟后我们打包 然后再发便条"
    )


def test_en_zh_mute_not_dumb():
    assert "静音" in postedit_en_zh(
        "No, don't mute yourself yet.", "不,不要哑巴自己还。"
    )
    assert "静音" in postedit_en_zh(
        "Are you muted? I'm getting silence on my end.",
        "你哑口无言吗 我这边的沉默越来越安静了",
    )
    # unrelated with no mute stays untouched
    zh = "请在电话后发送录音"
    assert postedit_en_zh("Please send the recording after the call.", zh) == zh


def test_en_zh_boundary_sense_direction_colloquial():
    assert "断" in postedit_en_zh(
        "The call drops every fifteen minutes or so.",
        "每十五分钟就接到一次电话",
    )
    assert "过一下" in postedit_en_zh(
        "Can you walk me through what broke in the last release real quick?",
        "你能告诉我 上次发布稿中 打破了什么 很快吗?",
    )
    assert "往后" in postedit_en_zh(
        "Can we push the deadline by a day?", "我们能把最后期限提前一天吗?"
    )
    assert "提前" not in postedit_en_zh(
        "Can we push the deadline by a day?", "我们能把最后期限提前一天吗?"
    )
    assert "上线前" in postedit_en_zh(
        "I thought we agreed not to change the API until after the rollout.",
        "我以为我们同意在推出后 才会改变API",
    )
    assert "挂" in postedit_en_zh("Gonna jump off in a sec.", "马上就跳下来")
    assert "断" in postedit_en_zh(
        "Sorry, you broke up there for a bit.", "抱歉 你刚才分手了"
    )
    # anti-break: relationship breakup / physical jump must stay
    rel = "她和他分手了"
    assert postedit_en_zh("She broke up with him last week.", rel) == rel
    phys = "别从桥上跳下去"
    assert postedit_en_zh("Don't jump off the bridge.", phys) == phys


def test_reply_message_direction():
    assert (
        postedit_zh_en("他现在没有回我的消息", "He hasn't heard back from me yet.")
        == "He hasn't replied to my messages yet."
    )
    assert (
        postedit_zh_en("啊现在没有回我的消息", "Ah, no news of me now.")
        == "He hasn't replied to my messages yet."
    )
    assert postedit_zh_en("你回我消息了吗", "Did you get my message?") == (
        "Did you reply to my message?"
    )


def test_interview_one_round():
    assert "one round" in postedit_zh_en(
        "我现在面试的这个公司只有一面",
        "I'm only interviewing one side of the company.",
    ).lower()
    assert "round" in postedit_zh_en(
        "我可能过不了这一面", "I may not pass this one side."
    ).lower()


def test_hear_weather_slow():
    assert "clearly" in postedit_zh_en(
        "我听不清你说什么", "I can't hear you."
    ).lower()
    assert postedit_zh_en("今天天气怎么样", "How was the weather today.").startswith(
        "How's the weather today"
    )
    assert "slow" in postedit_zh_en(
        "感觉你翻译的有点慢", "You're a little slow to translate."
    ).lower()


def test_unrelated_untouched():
    en = "Can you say that again?"
    assert postedit_zh_en("你能再说一遍吗", en) == en
