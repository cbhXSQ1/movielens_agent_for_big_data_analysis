// 对话树重做 · 纯函数单测：回合聚合（一问一答聚成一节点）+ 竖向 Git 图布局
// （父子/泳道分配、竖向坐标单调递增、分支新开泳道、连线端点）。
import { describe, it, expect } from 'vitest'
import {
  CANVAS_PAD,
  COL_GAP,
  ROW_GAP,
  buildRounds,
  layoutTree,
} from '@/components/tree/treeLayout'
import type { NodeRole, TreeNode } from '@/api/types'

function node(
  node_id: number,
  parent_id: number | null,
  role: NodeRole,
  seq: number,
  summary = '',
): TreeNode {
  return { node_id, parent_id, role, seq, summary, created_at: seq }
}

describe('回合聚合（buildRounds）', () => {
  it('一次提问 + 一次回复 = 一个回合（不再画成两个节点）', () => {
    const rounds = buildRounds([
      node(1, null, 'user', 1, '看一下数据量'),
      node(2, 1, 'assistant', 2, '已统计总行数'),
    ])

    expect(rounds).toHaveLength(1)
    expect(rounds[0].id).toBe(1)
    expect(rounds[0].userId).toBe(1)
    expect(rounds[0].nodes.map((n) => n.node_id)).toEqual([1, 2])
    expect(rounds[0].question).toBe('看一下数据量')
    expect(rounds[0].reply).toBe('已统计总行数')
    expect(rounds[0].parentId).toBeNull()
  })

  it('长任务续写（task / assistant）归入同一回合', () => {
    const rounds = buildRounds([
      node(1, null, 'user', 1, '跑清洗'),
      node(2, 1, 'assistant', 2, '已启动'),
      node(3, 2, 'task', 3, '任务进行中'),
      node(4, 3, 'assistant', 4, '任务完成'),
    ])

    expect(rounds).toHaveLength(1)
    expect(rounds[0].nodes.map((n) => n.node_id)).toEqual([1, 2, 3, 4])
    expect(rounds[0].reply).toBe('已启动\n任务进行中\n任务完成')
  })

  it('父子回合：父回合 = 该支上前一个 user 节点', () => {
    const rounds = buildRounds([
      node(1, null, 'user', 1, 'q1'),
      node(2, 1, 'assistant', 2, 'a1'),
      node(3, 2, 'user', 3, 'q2'),
      node(4, 3, 'assistant', 4, 'a2'),
    ])

    expect(rounds.map((r) => r.id)).toEqual([1, 3])
    expect(rounds[0].parentId).toBeNull()
    expect(rounds[1].parentId).toBe(1)
  })
})

describe('竖向 Git 图布局（layoutRounds / layoutTree）', () => {
  const branched: TreeNode[] = [
    node(1, null, 'user', 1, 'q1'),
    node(2, 1, 'assistant', 2, 'a1'),
    node(3, 2, 'user', 3, 'q2'),
    node(4, 3, 'assistant', 4, 'a2'),
    node(5, 2, 'user', 5, 'q2分支'),
    node(6, 5, 'assistant', 6, 'a2分支'),
  ]

  it('竖向坐标自上而下单调递增', () => {
    const layout = layoutTree(branched)
    const ys = layout.rounds.map((p) => p.y)
    for (let i = 1; i < ys.length; i += 1) {
      expect(ys[i]).toBeGreaterThan(ys[i - 1])
    }
    expect(layout.rounds[0].y).toBe(CANVAS_PAD)
    expect(layout.rounds[1].y).toBe(CANVAS_PAD + ROW_GAP)
  })

  it('父的第一个子回合沿用父泳道；分支新开泳道', () => {
    const layout = layoutTree(branched)
    const byId = new Map(layout.rounds.map((p) => [p.round.id, p]))

    expect(byId.get(1)?.lane).toBe(0)
    expect(byId.get(3)?.lane).toBe(0) // 父 1 的第一个子，沿用泳道 0
    expect(byId.get(5)?.lane).toBe(1) // 父 1 的第二个子，新开泳道 1
    expect(layout.laneCount).toBe(2)
    expect(byId.get(5)?.x).toBe(CANVAS_PAD + COL_GAP)
  })

  it('连线端点：同泳道竖线、跨泳道曲线', () => {
    const layout = layoutTree(branched)
    const to3 = layout.edges.find((e) => e.to === 3)
    const to5 = layout.edges.find((e) => e.to === 5)

    expect(to3).toBeDefined()
    expect(to3?.from).toBe(1)
    expect(to3?.curved).toBe(false)
    expect(to3?.x1).toBe(to3?.x2) // 同泳道 → 竖直

    expect(to5).toBeDefined()
    expect(to5?.from).toBe(1)
    expect(to5?.curved).toBe(true)
    expect(to5?.x1).not.toBe(to5?.x2) // 跨泳道 → 水平错开
  })

  it('画布尺寸随泳道数 / 回合数增长', () => {
    const single = layoutTree([node(1, null, 'user', 1, 'q1')])

    // 一个根 + 从它分出的 4 个兄弟回合 → 4 条泳道。
    const wide: TreeNode[] = [node(1, null, 'user', 1, 'root')]
    for (let i = 1; i <= 4; i += 1) {
      wide.push(node(1 + i, 1, 'user', 1 + i, `q${i}`))
    }

    // 8 个回合的单链 → 高度随时间增长。
    const deep: TreeNode[] = []
    for (let i = 0; i < 8; i += 1) {
      const userId = i * 2 + 1
      deep.push(node(userId, i === 0 ? null : userId - 2, 'user', userId, `q${i}`))
      deep.push(node(userId + 1, userId, 'assistant', userId + 1, `a${i}`))
    }

    const wideLayout = layoutTree(wide)
    const deepLayout = layoutTree(deep)

    expect(wideLayout.laneCount).toBe(4)
    expect(wideLayout.width).toBeGreaterThan(single.width)
    expect(deepLayout.height).toBeGreaterThan(single.height)
  })
})
