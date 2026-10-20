// 对话树布局（重做版）：把后端返回的**扁平节点**先聚成「回合」，再排成
// **竖向 Git 提交图**（自上而下、每条分支占一条泳道）。纯函数、无副作用，便于单测。
//
// 语义：
// - 一个「回合」= 一个 `user` 节点 + 其后到下一个 `user` 节点之前的 assistant/task
//   节点（都在同一支内）。回合的规范 id 用它包含的 `user` 节点的 id。
// - 回合之间的父子：某回合的父回合 = 该支上**前一个**回合（沿 parent 链向上找到
//   最近的前一个 user 节点；找不到则是根）。
//
// 布局（竖着的 `git log --graph`）：
// - 时间自上而下：y = 序号 * ROW_GAP。
// - 每条分支占一列泳道：x = 泳道 * COL_GAP。
// - 同泳道父子画竖线；跨泳道（分支/汇合）画贝塞尔曲线。画布尺寸随回合数/泳道数增长。

import type { NodeId, TreeNode } from '@/api/types'

/** 回合圆点半径 / 列距 / 行距 / 画布内边距（画布单位 px）。 */
export const ROUND_R = 16
export const COL_GAP = 200
export const ROW_GAP = 88
export const CANVAS_PAD = 48
export const MIN_CANVAS_W = 640
export const MIN_CANVAS_H = 420
/** 圆点旁问题摘要的宽度（用于计算画布最小宽度）。
 *  加宽到能放下「#序号 + 两行提问 + 一行回复摘要」——窄了用户分不清哪个是哪个。 */
export const LABEL_W = 300

/** 一个「回合」：一次提问 + 一次回复（含其间的 task/assistant 续写节点）。 */
export interface Round {
  /** 规范 id：本回合所含 `user` 节点的 node_id（无 user 时回落成根节点 id）。 */
  id: NodeId
  /** 所含 `user` 节点的 id；异常数据下可能为 null。 */
  userId: NodeId | null
  user: TreeNode | null
  /**
   * 本回合**末尾节点** id（一次问答里的最后一条，通常是助手回复）。
   * 「在此继续」用它作分支点：这样整轮（问 + 答）都留在新支里，
   * 只把**该轮之后**的消息当作旧支隐藏。
   */
  tipId: NodeId
  /** 父回合 id（= 前一个 user 节点 id）；根为 null。 */
  parentId: NodeId | null
  /** 排序用序号（取本回合节点里的最小 seq）。 */
  seq: number
  /** 本回合包含的全部节点，按 seq 升序。 */
  nodes: TreeNode[]
  /** 提问摘要（user 节点 summary）。 */
  question: string
  /** 回复摘要（assistant/task 节点 summary 拼接）。 */
  reply: string
}

/** 完成坐标计算的回合圆点。 */
export interface PositionedRound {
  round: Round
  lane: number
  index: number
  x: number
  y: number
}

/** 一条父子连线；`curved` 为真时表示跨泳道（分支/汇合），画贝塞尔曲线。 */
export interface TreeEdge {
  from: NodeId
  to: NodeId
  x1: number
  y1: number
  x2: number
  y2: number
  curved: boolean
}

export interface TreeLayout {
  rounds: PositionedRound[]
  edges: TreeEdge[]
  width: number
  height: number
  laneCount: number
}

function bySeq(a: TreeNode, b: TreeNode): number {
  return a.seq - b.seq || a.node_id - b.node_id
}

/**
 * 把扁平节点聚成「回合」。一次提问 + 一次回复聚成一回合（不再是两个节点）。
 * 从左到右按 seq 升序返回，父回合保证排在子回合之前。
 */
export function buildRounds(nodes: TreeNode[]): Round[] {
  const byId = new Map<NodeId, TreeNode>()
  for (const n of nodes) byId.set(n.node_id, n)

  const childrenOf = new Map<NodeId, TreeNode[]>()
  for (const n of nodes) {
    const pid = n.parent_id
    if (pid !== null && byId.has(pid)) {
      const list = childrenOf.get(pid)
      if (list) list.push(n)
      else childrenOf.set(pid, [n])
    }
  }
  for (const list of childrenOf.values()) list.sort(bySeq)

  const used = new Set<NodeId>()

  /** 从起点收集节点；遇到别的 user 节点即止（那是下一个回合的边界）。 */
  function collect(start: TreeNode): TreeNode[] {
    const out: TreeNode[] = []
    const stack: TreeNode[] = [start]
    while (stack.length) {
      const cur = stack.pop()
      if (!cur || used.has(cur.node_id)) continue
      used.add(cur.node_id)
      out.push(cur)
      for (const kid of childrenOf.get(cur.node_id) ?? []) {
        if (kid.role === 'user') continue
        stack.push(kid)
      }
    }
    out.sort(bySeq)
    return out
  }

  /** 沿 parent 链向上找最近的前一个 user 节点（= 父回合 id）。 */
  function parentRoundId(node: TreeNode): NodeId | null {
    let pid = node.parent_id
    while (pid !== null) {
      const p = byId.get(pid)
      if (!p) return null
      if (p.role === 'user') return p.node_id
      pid = p.parent_id
    }
    return null
  }

  function makeRound(user: TreeNode | null, members: TreeNode[]): Round {
    const sorted = [...members].sort(bySeq)
    const id = user ? user.node_id : (sorted[0]?.node_id ?? 0)
    const question = (user?.summary ?? sorted[0]?.summary ?? '').trim()
    const reply = sorted
      .filter((n) => n.role !== 'user')
      .map((n) => (n.summary ?? '').trim())
      .filter((s) => s.length > 0)
      .join('\n')
    const tip = sorted[sorted.length - 1]
    return {
      id,
      userId: user ? user.node_id : null,
      user,
      tipId: tip ? tip.node_id : id,
      parentId: user ? parentRoundId(user) : null,
      seq: sorted[0]?.seq ?? 0,
      nodes: sorted,
      question,
      reply,
    }
  }

  const rounds: Round[] = []
  const users = nodes.filter((n) => n.role === 'user').sort(bySeq)
  for (const u of users) {
    if (used.has(u.node_id)) continue
    rounds.push(makeRound(u, collect(u)))
  }
  // 兜底：没有 user 祖先的孤立节点（异常数据）各自成回合，保证全部可见。
  for (const n of [...nodes].sort(bySeq)) {
    if (used.has(n.node_id)) continue
    rounds.push(makeRound(n.role === 'user' ? n : null, collect(n)))
  }

  rounds.sort((a, b) => a.seq - b.seq || a.id - b.id)
  return rounds
}

/** 给回合分配泳道并计算竖向坐标 / 连线 / 画布尺寸。 */
export function layoutRounds(rounds: Round[]): TreeLayout {
  const ordered = [...rounds].sort((a, b) => a.seq - b.seq || a.id - b.id)

  // 泳道分配：按 seq 顺序处理；父回合所在的泳道若仍「活跃」且指向父回合，
  // 则沿用该泳道（父的第一个子回合继承），否则新开一条泳道（分支）。
  const laneOf = new Map<NodeId, number>()
  const active: NodeId[] = []
  let laneCount = 0
  for (const r of ordered) {
    let lane = -1
    if (r.parentId !== null) {
      lane = active.findIndex((a) => a === r.parentId)
    }
    if (lane < 0) {
      lane = laneCount
      laneCount += 1
    }
    active[lane] = r.id
    laneOf.set(r.id, lane)
  }

  const positioned: PositionedRound[] = ordered.map((round, index) => {
    const lane = laneOf.get(round.id) ?? 0
    return {
      round,
      lane,
      index,
      x: CANVAS_PAD + lane * COL_GAP,
      y: CANVAS_PAD + index * ROW_GAP,
    }
  })

  const posById = new Map<NodeId, PositionedRound>()
  for (const p of positioned) posById.set(p.round.id, p)

  const edges: TreeEdge[] = []
  for (const p of positioned) {
    const parentId = p.round.parentId
    if (parentId === null) continue
    const parent = posById.get(parentId)
    if (!parent) continue
    edges.push({
      from: parent.round.id,
      to: p.round.id,
      x1: parent.x,
      y1: parent.y,
      x2: p.x,
      y2: p.y,
      curved: parent.lane !== p.lane,
    })
  }

  const maxLane = positioned.reduce((m, p) => Math.max(m, p.lane), 0)
  const width = Math.max(
    MIN_CANVAS_W,
    CANVAS_PAD * 2 + maxLane * COL_GAP + LABEL_W,
  )
  const height = Math.max(
    MIN_CANVAS_H,
    CANVAS_PAD * 2 + Math.max(positioned.length - 1, 0) * ROW_GAP + ROUND_R * 2,
  )

  return { rounds: positioned, edges, width, height, laneCount }
}

/** 便捷入口：扁平节点一次算好回合聚合 + 竖向布局。 */
export function layoutTree(nodes: TreeNode[]): TreeLayout {
  return layoutRounds(buildRounds(nodes))
}
