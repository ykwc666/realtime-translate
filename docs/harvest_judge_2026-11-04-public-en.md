# Harvest 人判表 — 2026-11-04-public-en

来源采集：`data/harvest/2026-11-04-public-en.jsonl`
生成时间：2026-11-04 19:58:22

## 判定标准（两次会话必须同一尺子）

| 档 | 含义 |
|----|------|
| **ok** | 与 src 语义等价，口语自然，可直接播给中文听众 |
| **awkward** | 能懂，但不自然 / 术语别扭 / 语序怪（听众要愣一下） |
| **wrong** | 语义错、极性反、关键信息丢、或听不懂 |

- 只看 **tgt**（发给 TTS 的最终译文）相对 **src**。
- `mt_raw` 仅用于分组：`mt_raw != tgt` → 规则层/TM 修过；相等 → 模型原样出。
- 不要用字符串相似度自动判；本文件人判列必须手填。


## 待判表

| # | src | mt_raw | tgt | rule_touched | verdict |
|---|-----|--------|-----|--------------|---------|
| 0 | even though there all the other same topics，where doesn't。 | 尽管其他所有没有 | 尽管其他所有没有 | no |  |
| 1 | This would , yeah。 | 这会,是的 | 这会,是的 | no |  |
| 2 | Okay。 | 好的。 | 好的。 | no |  |
| 3 | Police superintendent Shara Shankhar Soenssky said he。 | 警监沙拉·尚哈尔·苏恩斯基说 | 警监沙拉·尚哈尔·苏恩斯基说 | no |  |
| 4 | use sappear in court with covered faces。 | 面罩遮盖的出庭时出现 | 面罩遮盖的出庭时出现 | no |  |
| 5 | He hoped there would be stew for dinner，, turnips and carrots and bruised potatoes。 | 他希望有炖菜吃晚饭、菜菜菜、萝卜、胡萝卜和土豆 | 他希望有炖菜吃晚饭、菜菜菜、萝卜、胡萝卜和土豆 | no |  |
| 6 | Potoes and fat mutton pieces to be ladled out in thick , peppered flower fattened sauce。 | 将薄荷和肥羊肉碎片 混合在厚厚的辣椒花脂酱中 | 将薄荷和肥羊肉碎片 混合在厚厚的辣椒花脂酱中 | no |  |
| 7 | fefered to his belly councelled him。 | 被他的肚子咬伤 | 被他的肚子咬伤 | no |  |
| 8 | For early nightfall ,，the yellow lamps would light up here and there。 | 黄灯照亮了天上和天上 | 黄灯照亮了天上和天上 | no |  |
| 9 | there the squad id quarter of the broffles。 | 在那里,队 ID四分之一的 支支支架 | 在那里,队 ID四分之一的 支支支架 | no |  |
| 10 | no，berty ty，any good in your mnumber number。 | 你的编号没有好的 | 你的编号没有好的 | no |  |
| 11 | and freshnaally is waiting on you。 | 新鲜的等待著你, | 新鲜的等待著你, | no |  |
| 12 | ight husband ,，the music came nearer ,，and he recalled the words ,，the words of Shelllly。 | 丈夫,音乐更近了, 他回忆起雪利的话, | 丈夫,音乐更近了, 他回忆起雪利的话, | no |  |
| 13 | fragment upon the moon wanddering companionless pale for wearingness。the。 | 月亮上浮露着白白的白白, | 月亮上浮露着白白的白白, | no |  |
| 14 | The light fell more faintly upon the page，whereon another equation began to unfold itself slowly and to。 | 另一方程式开始慢慢地 慢慢地 慢慢地 慢慢地 慢慢地 慢慢地 | 另一方程式开始慢慢地 慢慢地 慢慢地 慢慢地 慢慢地 慢慢地 | no |  |
| 15 | ababroad it's whdening tail a cold lusit in different ranrained in his sou。 | 在一个不同的沙丘里 被洗刷过 | 在一个不同的沙丘里 被洗刷过 | no |  |
| 16 | The chaos in which his ardor extinguished itself was a cold , indifferent knowledge of himself at most。 | 他的摇摆消灭了自己的混乱 是一个寒冷, 冷漠的 对自己了解最多 | 他的摇摆消灭了自己的混乱 是一个寒冷, 冷漠的 对自己了解最多 | no |  |
| 17 | by an arms given to a beggar，whose blessing he fled from。 | 以武器供给乞丐,而他从他们的恩惠中逃出; | 以武器供给乞丐,而他从他们的恩惠中逃出; | no |  |
| 18 | might hope weirily to win for himself some measure of actual graace。 | 希望能为自己赢得 某种程度的实际的肉汁 | 希望能为自己赢得 某种程度的实际的肉汁 | no |  |
| 19 | Now , Ennis ,，I declare you have a head ,，and so has my stick on Saturday。 | 现在,恩尼斯,我宣布你有一个头, 我的棍子在星期六也是一样 | 现在,恩尼斯,我宣布你有一个头, 我的棍子在星期六也是一样 | no |  |
| 20 | mornings when the sodality met in the chapel to recite the。 | 早晨,善功在礼拜堂里相会,诵读 | 早晨,善功在礼拜堂里相会,诵读 | no |  |
| 21 | Little office .，His place was a cushioned kneeling desk at the right of the altar，from which he LED his wing of boys through the responses。 | 办公室小,他家在圣坛右侧的 跪着的桌子上 | 办公室小,他家在圣坛右侧的 跪着的桌子上 | no |  |
| 22 | Her eyes seemed to regard him with mild pity。, her holiness。 | 她的眼睛似乎以温和的怜悯和圣洁看待他 | 她的眼睛似乎以温和的怜悯和圣洁看待他 | no |  |
| 23 | Strange light glowing faintly upon her frail flesh did not humiliate the sinner who approached her。 | 微微闪耀着微弱的皮肤 的奇异光光照着她的身体 并没有羞辱接近她的罪人 | 微微闪耀着微弱的皮肤 的奇异光光照着她的身体 并没有羞辱接近她的罪人 | no |  |
| 24 | If ever，he was impelled to cast sin from him and to repent，the impulse that。 | 如果他被逼从他身上抛出罪孽 并且忏悔那冲动 | 如果他被逼从他身上抛出罪孽 并且忏悔那冲动 | no |  |
| 25 | Him was the wish to be hernight。 | 他的愿望是成为她的夜晚 | 他的愿望是成为她的夜晚 | no |  |
| 26 | He tried to think how it could be .，But the dusk deepening in the schoolroom covered over his thought。 | 却在教室深夜中 满身都是他的思念 | 却在教室深夜中 满身都是他的思念 | no |  |
| 27 | the bell rthen，then you can ask him court。 | 钟声响起,然后你可以问他法庭 | 钟声响起,然后你可以问他法庭 | no |  |
| 28 | 旋转的the acacisism deadalless。 | 骨髓结晶主义无生命 | 骨髓结晶主义无生命 | no |  |
| 29 | Even leaning back and drawing idly on his scribbler listened to the talk about him ,，which Heron checked from time to time by saying。 | 连靠靠靠靠着, 画着他的书写者 听了有关他的演讲, Heron不时地检查 | 连靠靠靠靠着, 画着他的书写者 听了有关他的演讲, Heron不时地检查 | no |  |
| 30 | it was strange，too，that he found an arrored d pleasure in following up to the end，the rigid lines of the docttrons of the church。 | 也很奇怪 他发现在追逐教堂的 门窗的死板线 之后 有一种不愉快的快乐 | 也很奇怪 他发现在追逐教堂的 门窗的死板线 之后 有一种不愉快的快乐 | no |  |
| 31 | penetraating into obscure silences only to hear and feel the more deeply his own demnation the same。 | 沉沉在模糊的沉默中 只能听到和感受他 自己沉沉的内心 | 沉沉在模糊的沉默中 只能听到和感受他 自己沉沉的内心 | no |  |
| 32 | of sof sajjames，which says that he who offends against one commandment becomes guilty。 | 说犯过一项命令的人 有罪 | 说犯过一项命令的人 有罪 | no |  |
| 33 | 哦had semed to him first，a swol in phrase until he had begun to groroup the darkness of his zones state ph。 | 何以先是一言一语的狼人 开始在他所在的 地区黑暗中 起黑暗 | 何以先是一言一语的狼人 开始在他所在的 地区黑暗中 起黑暗 | no |  |
| 34 | man man stolled on a pound in his youth and had used that pound to a mass，a huge。 | 男人在青春中用一磅 将一磅的肉 用来做一个巨大的质量 | 男人在青春中用一磅 将一磅的肉 用来做一个巨大的质量 | no |  |
| 35 | fortune，how much was he obliged to give back the pound？ | 算他多少钱 不得不还回他那磅 | 算他多少钱 不得不还回他那磅 | no |  |
| 36 | He had stolen only or the pound together with the compound。 | 他偷的只有他 或者他把硬币和院子一起偷 | 他偷的只有他 或者他把硬币和院子一起偷 | no |  |
| 37 | inteinteres。 | 内插内 | 内插内 | no |  |
| 38 | Srewing upon it or all his huge fortune . if a layman。 | 偷取它或他所有的巨大财富 如果一个外行人 | 偷取它或他所有的巨大财富 如果一个外行人 | no |  |
| 39 | giving baptism pour the water before saying the word is the chil。 | 洗礼在说道前先倒水 说道:"这句是"这句" | 洗礼在说道前先倒水 说道:"这句是"这句" | no |  |
| 40 | wd baptized，how comes it that while the first be attitude promises the kingdom of heaven to the poor heheart，the second be attitude，prob。 | 洗礼 如何来 如何来, 一方面是态度 第一是态度 向天国 向穷人天堂  hehearts 的 第二项是态度 | 洗礼 如何来 如何来, 一方面是态度 第一是态度 向天国 向穷人天堂  hehearts 的 第二项是态度 | no |  |
| 41 | mois also to the mek that they shp possess sed land。why was was saccrement of the eukrist instituted under the two species of bread and wine，if jesus c。 | 也向麦水说他们打草草 拥有有土地的 红地 为什么是亵渎 秋菊 建立在两种面包和葡萄酒的 | 也向麦水说他们打草草 拥有有土地的 红地 为什么是亵渎 秋菊 建立在两种面包和葡萄酒的 | no |  |
| 42 | Christ be present，, body and blood , soul and divinity in the bread alone and in the wine alone。 | 基督在面包中 独自在酒中 独自在面包中 身体和血血 灵魂和神灵 | 基督在面包中 独自在酒中 独自在面包中 身体和血血 灵魂和神灵 | no |  |
| 43 | If the wine change into vinegar and the host crumble into corruption after they have。 | 如果葡萄酒变成醋,而酒主在酒后变成腐烂, | 如果葡萄酒变成醋,而酒主在酒后变成腐烂, | no |  |
| 44 | in consequcreted is jesus christ still present under their species as god and as man。 | 圣洁的耶稣基督 仍然存在于他们的物种下 作为神和人类 | 圣洁的耶稣基督 仍然存在于他们的物种下 作为神和人类 | no |  |
| 45 | 头kick from the toall boy in the bench，behind urged steven to ask it difficult question。 | 被坐在后座的男儿童踢出场,敦促史蒂芬提出困难的问题 | 被坐在后座的男儿童踢出场,敦促史蒂芬提出困难的问题 | no |  |
| 46 | rector did not ask for a categism to hear the the ssson he，he laas。 | 校长没有要求 分类 听他所讲的 | 校长没有要求 分类 听他所讲的 | no |  |
| 47 | 太太ands on the desk and said the re。 | 桌子上说 | 桌子上说 | no |  |
| 48 | TheTreat will begin on Wednesday afternoon in honor of Saint Francis Xavier。, whose feast day is Saturday on Friday。 | 周三下午开始 庆祝圣弗朗西斯·泽维尔 星期五是他的盛宴日 | 周三下午开始 庆祝圣弗朗西斯·泽维尔 星期五是他的盛宴日 | no |  |
| 49 | Confession will be heard all the。 | 所有的忏悔都会被听到 | 所有的忏悔都会被听到 | no |  |

## 填判方式

1. 编辑 `D:/xuanjian/realtime-translate/docs/harvest_judge_2026-11-04-public-en.jsonl`，把每行 `verdict` 填成 `ok` / `awkward` / `wrong`。
2. 汇总：`python scripts/judge_harvest.py --ingest D:/xuanjian/realtime-translate/docs/harvest_judge_2026-11-04-public-en.jsonl`

