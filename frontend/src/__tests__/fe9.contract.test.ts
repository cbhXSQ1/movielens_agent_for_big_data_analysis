// FE-9 · 收口：把早前评审里点到、但组件级用例尚未覆盖的几处契约补齐。
// 只补真实缺口，不重复既有断言：
//   - 进行中提示只对「排队 / 运行」出现，终态（含成功 / 失败）一律不出现；
//   - 已中止任务的进度停在中断处（事件流既不重置、也不补到 100%）；
//   - 产物类别辅助函数的徽标配色 / 日志识别 / 路径归类（此前只有组件级间接覆盖）。
// `RESULT_BLOCK_META` 六块与下载地址指向 Agent 的断言已分别在 results.test.ts /
// resources.test.ts 覆盖，此处不再重复。
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import {
  abortedHint,
  activeHint,
} from '@/components/progress/taskDisplay'
import {
  categoryTagType,
  deriveCategoryFromPath,
  isLogArtifact,
} from '@/components/results/artifactCategory'
import TaskListItem from '@/components/progress/TaskListItem.vue'
import { useTasksStore } from '@/stores/tasks'
import type { TaskStatus, TaskView } from '@/api/types'

function task(over: Partial<TaskView> = {}): TaskView {
  return {
    agent_task_id: 'x1',
    hadoop_task_id: null,
    name: '任务',
    summary: '',
    status: 'running',
    status_cn: '运行',
    stage: '',
    progress_percent: 0,
    estimated_seconds: null,
    updated_at: 0,
    ...over,
  }
}

describe('FE-9 · 进行中提示只在非终态出现', () => {
  it('activeHint 仅对排队/运行给提示；abortedHint 仅对已中止给提示，其余状态均为 null', () => {
    expect(activeHint('queued')).toContain('仍在运行')
    expect(activeHint('running')).toContain('仍在运行')
    for (const s of ['succeeded', 'failed', 'aborted'] as TaskStatus[]) {
      expect(activeHint(s)).toBeNull()
    }

    const note = abortedHint('aborted')
    expect(note).toContain('已中止')
    expect(note).toContain('中断处')
    for (const s of ['queued', 'running', 'succeeded', 'failed'] as TaskStatus[]) {
      expect(abortedHint(s)).toBeNull()
    }
  })

  it('成功/失败任务的列表项不渲染任何进行中或中止提示', () => {
    for (const status of ['succeeded', 'failed'] as TaskStatus[]) {
      const wrapper = mount(TaskListItem, {
        props: { task: task({ status, progress_percent: 100 }) },
      })
      expect(
        wrapper.find('[data-testid="task-running-hint-x1"]').exists(),
      ).toBe(false)
      expect(
        wrapper.find('[data-testid="task-aborted-hint-x1"]').exists(),
      ).toBe(false)
    }
  })
})

describe('FE-9 · 已中止进度停在中断处（事件流不重置、不补到 100%）', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('运行到 40% 后收到省略进度的中止事件，进度仍为 40%', () => {
    const tasks = useTasksStore()
    tasks.applyStatus(task({ progress_percent: 40, stage: '清洗' }))
    expect(tasks.get('x1')?.progress_percent).toBe(40)

    // 中止事件照远端语义可能不带 progress_percent（键缺失）；已显示的进度应原样保留。
    tasks.applyStatus({
      agent_task_id: 'x1',
      hadoop_task_id: null,
      name: '任务',
      summary: '',
      status: 'aborted',
      status_cn: '已中止',
      stage: '评分',
      estimated_seconds: null,
      updated_at: 0,
    } as unknown as TaskView)

    const t = tasks.get('x1')
    expect(t?.status).toBe('aborted')
    expect(t?.progress_percent).toBe(40)
    expect(t?.progress_percent).not.toBe(100)
  })
})

describe('FE-9 · 产物类别的展示契约（徽标配色 / 日志识别 / 路径归类）', () => {
  it('类别徽标配色固定，未知/缺失类别回落 default', () => {
    expect(categoryTagType('数据')).toBe('info')
    expect(categoryTagType('隔离')).toBe('error')
    expect(categoryTagType('报告')).toBe('success')
    expect(categoryTagType('指标')).toBe('warning')
    expect(categoryTagType('元数据')).toBe('warning')
    expect(categoryTagType('日志')).toBe('default')
    expect(categoryTagType('其它')).toBe('default')
    expect(categoryTagType(undefined)).toBe('default')
    expect(categoryTagType('没见过的类别')).toBe('default')
  })

  it('日志识别覆盖类别、logs/ 路径与 .log 文件名；路径归类粗分各类别', () => {
    expect(isLogArtifact({ category: '日志' })).toBe(true)
    expect(isLogArtifact({ path: 'a/logs/run.txt' })).toBe(true)
    expect(isLogArtifact({ path: 'out/driver.log' })).toBe(true)
    expect(isLogArtifact({ title: 'driver.log' })).toBe(true)
    expect(isLogArtifact({ category: '报告', path: 'report.md' })).toBe(false)

    expect(deriveCategoryFromPath('logs/driver.log')).toBe('日志')
    expect(deriveCategoryFromPath('quarantine/R2.dat')).toBe('隔离')
    expect(deriveCategoryFromPath('out/report.md')).toBe('报告')
    expect(deriveCategoryFromPath('metrics/scores.json')).toBe('指标')
    expect(deriveCategoryFromPath('meta/schema.json')).toBe('元数据')
    expect(deriveCategoryFromPath('cleaned/ratings.dat')).toBe('其它')
    expect(deriveCategoryFromPath('')).toBe('其它')
  })
})
