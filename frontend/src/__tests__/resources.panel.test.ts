// 阶段 G · FG.1–FG.5：资源面板组件测试。
// 只伪造全局 fetch（假后端），组件→store→api→http 全链真实执行，
// 因而能直接断言真实构造出的 multipart FormData。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import ResourcesPanel from '@/views/panels/ResourcesPanel.vue'
import { useConnectionStore } from '@/stores/connection'
import { useTasksStore } from '@/stores/tasks'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { fileTabKey, schemeTabKey } from '@/components/centerTabs'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

interface Handlers {
  raw: () => Response
  published: () => Response
  schemes: () => Response
  schemeUpload: () => Response
  schemeDelete: () => Response
  taskFiles: () => Response
}

let handlers: Handlers
let fetchMock: ReturnType<typeof vi.fn>

function installFetch(): void {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input)).pathname
    switch (path) {
      case '/api/data/raw':
        return handlers.raw()
      case '/api/data/published':
        return handlers.published()
      case '/api/schemes':
        return handlers.schemes()
      case '/api/schemes/upload':
        return handlers.schemeUpload()
      case '/api/schemes/delete':
        return handlers.schemeDelete()
      default:
        if (/^\/api\/tasks\/[^/]+\/files$/.test(path)) return handlers.taskFiles()
        return jsonResponse({ ok: true })
    }
  })
  vi.stubGlobal('fetch', fetchMock)
}

function mountPanel(): VueWrapper {
  const pinia = createPinia()
  setActivePinia(pinia)
  const connection = useConnectionStore()
  connection.markStreamOpen()
  return mount(ResourcesPanel, { global: { plugins: [pinia] } })
}

function setFiles(wrapper: VueWrapper, testid: string, files: File[]): Promise<void> {
  const input = wrapper.find(`[data-testid="${testid}"]`)
  Object.defineProperty(input.element, 'files', {
    value: files,
    configurable: true,
  })
  return input.trigger('change')
}

/** 点某个方案的删除入口，并在 Naive UI 确认弹层里点「删除」。 */
async function confirmSchemeDelete(
  wrapper: VueWrapper,
  index: number,
): Promise<void> {
  await wrapper.find(`[data-testid="scheme-delete-${index}"]`).trigger('click')
  await flushPromises()
  const buttons = Array.from(
    document.querySelectorAll('.n-popconfirm__action button'),
  ) as HTMLButtonElement[]
  const confirm = buttons.find((b) => (b.textContent ?? '').includes('删除'))
  expect(confirm).toBeDefined()
  confirm?.click()
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  handlers = {
    raw: () => jsonResponse({ ok: true, dir: '/raw', datasets: [] }),
    published: () => jsonResponse({ ok: true, datasets: [] }),
    schemes: () => jsonResponse({ ok: true, schemes: [] }),
    schemeUpload: () =>
      jsonResponse({
        ok: true,
        saved: { name: 'scoring_scheme.v1.json', path: 'config/scoring_scheme.v1.json' },
        validation: { ok: true },
      }),
    schemeDelete: () =>
      jsonResponse({
        ok: true,
        deleted: true,
        scheme: { path: 'config/my_custom.v1.json' },
        schemes: [],
        note: '删除不可恢复',
      }),
    taskFiles: () => jsonResponse({ ok: true, task_id: 't1', files: [] }),
  }
  installFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document
    .querySelectorAll('.v-binder-follower-container')
    .forEach((node) => node.remove())
})

describe('FG.1 · 资源面板四段框架', () => {
  it('渲染四段与对应标签，连接就绪后拉取三段列表', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="panel-resources"]').exists()).toBe(true)
    for (const seg of ['raw', 'published', 'schemes', 'files']) {
      expect(wrapper.find(`[data-testid="resources-tab-${seg}"]`).exists()).toBe(true)
      expect(wrapper.find(`[data-testid="resources-segment-${seg}"]`).exists()).toBe(true)
    }
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).toContain('/api/data/raw')
    expect(paths).toContain('/api/data/published')
    expect(paths).toContain('/api/schemes')
    expect(wrapper.find('[data-testid="raw-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="published-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="schemes-empty"]').exists()).toBe(true)
  })
})

describe('FG.2 · 原始数据：只读列表 + 提示（远端不提供上传）', () => {
  it('不再渲染上传按钮，并显示维护位置提示', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="raw-upload-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="raw-upload-input"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="raw-upload-name"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('原始数据在运行数据的机器上维护')

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).not.toContain('/api/data/raw/upload')
  })

  it('只读列表照常渲染数据集', async () => {
    handlers.raw = () =>
      jsonResponse({
        ok: true,
        dir: '/raw',
        datasets: [
          { name: 'ml-1m', path: 'ml-1m', files: [], size: 2048, updated_at: 1729000000 },
        ],
      })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="raw-dataset-ml-1m"]').exists()).toBe(true)
  })
})

describe('FG.4 · 方案文件：上传回显 validation 与查看开标签', () => {
  it('上传 .json 后用响应回显校验结果', async () => {
    handlers.schemeUpload = () =>
      jsonResponse({
        ok: true,
        saved: { name: 'scoring_scheme.v1.json', path: 'config/scoring_scheme.v1.json' },
        validation: { ok: false, errors: [{ message: '缺字段 rules' }] },
      })
    const wrapper = mountPanel()
    await flushPromises()

    await setFiles(wrapper, 'scheme-upload-input', [
      new File(['{}'], 'scoring_scheme.v1.json'),
    ])
    await flushPromises()

    const validation = wrapper.find('[data-testid="scheme-validation"]')
    expect(validation.exists()).toBe(true)
    expect(validation.text()).toContain('校验未通过')
    expect(validation.text()).toContain('缺字段 rules')
    // 上传后 store 就地重列方案
    const schemeCalls = fetchMock.mock.calls.filter(
      ([url]) => new URL(String(url)).pathname === '/api/schemes',
    )
    expect(schemeCalls.length).toBeGreaterThanOrEqual(2)
  })

  it('覆盖同名方案：回显「已覆盖同名方案」提示', async () => {
    handlers.schemeUpload = () =>
      jsonResponse({
        ok: true,
        saved: { name: 'scoring_scheme.v1.json', path: 'config/scoring_scheme.v1.json' },
        validation: { ok: true },
        overwritten: true,
        size_bytes: 42,
      })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="scheme-overwritten-note"]').exists()).toBe(
      false,
    )

    await setFiles(wrapper, 'scheme-upload-input', [
      new File(['{}'], 'scoring_scheme.v1.json'),
    ])
    await flushPromises()

    const note = wrapper.find('[data-testid="scheme-overwritten-note"]')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('覆盖')
    expect(note.text()).toContain('同名')
  })

  it('点「查看」把方案登记为中栏资源标签（不再左栏内联内容）', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        schemes: [{ name: 'scoring_scheme.v1.json', path: 'config/scoring_scheme.v1.json' }],
      })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="scheme-view-0"]').trigger('click')
    await flushPromises()

    const centerTabs = useCenterTabsStore()
    expect(centerTabs.isOpen(schemeTabKey('scoring_scheme.v1.json'))).toBe(true)
    expect(wrapper.find('[data-testid="scheme-content"]').exists()).toBe(false)

    // 内容改由中栏 SchemeContentView 拉取；左栏不再内联请求
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).not.toContain('/api/schemes/content')
  })

  it('列表只给 path 时，查看按文件名开标签（label / payload 均为裸文件名）', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        schemes: [{ path: 'config/cleaning_rules.v1.json' }],
      })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="scheme-view-0"]').trigger('click')
    await flushPromises()

    const centerTabs = useCenterTabsStore()
    const tab = centerTabs.tabs.find(
      (item) => item.key === schemeTabKey('cleaning_rules.v1.json'),
    )
    expect(tab).toBeDefined()
    expect(tab?.label).toBe('cleaning_rules.v1.json')
    expect(tab?.payload).toEqual({ name: 'cleaning_rules.v1.json' })
    expect(centerTabs.isOpen(schemeTabKey('config/cleaning_rules.v1.json'))).toBe(
      false,
    )
    wrapper.unmount()
  })
})

describe('FE.11 · 方案文件：删除自定义方案（默认方案不可删）', () => {
  it('两份默认方案不显示删除入口，自定义方案显示', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        // 真实 driver 列表只给 path（`config/x.json`），默认方案识别需按文件名。
        schemes: [
          { path: 'config/cleaning_rules.v1.json' },
          { path: 'config/scoring_scheme.v1.json' },
          { path: 'config/my_custom.v1.json' },
        ],
      })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="scheme-delete-0"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="scheme-delete-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="scheme-delete-2"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('确认后调用删除接口（单层文件名），并刷新方案列表', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        schemes: [{ path: 'config/my_custom.v1.json' }],
      })
    const wrapper = mountPanel()
    await flushPromises()

    await confirmSchemeDelete(wrapper, 0)

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).toContain('/api/schemes/delete')
    const deleteCalls = fetchMock.mock.calls.filter(
      ([url]) => new URL(String(url)).pathname === '/api/schemes/delete',
    )
    expect(deleteCalls.length).toBe(1)
    expect(JSON.parse(String((deleteCalls[0][1] as RequestInit).body))).toEqual({
      name: 'my_custom.v1.json',
    })
    const schemeCalls = fetchMock.mock.calls.filter(
      ([url]) => new URL(String(url)).pathname === '/api/schemes',
    )
    expect(schemeCalls.length).toBeGreaterThanOrEqual(2)
    wrapper.unmount()
  })

  it('TASK_ALREADY_RUNNING → 提示「有任务在运行，等它结束再删」', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        schemes: [{ name: 'my_custom.v1.json', path: 'config/my_custom.v1.json' }],
      })
    handlers.schemeDelete = () =>
      jsonResponse({
        ok: false,
        error: {
          code: 'TASK_ALREADY_RUNNING',
          message: 'busy',
          occupier: 't1',
        },
      })
    const wrapper = mountPanel()
    await flushPromises()

    await confirmSchemeDelete(wrapper, 0)

    const error = wrapper.find('[data-testid="scheme-delete-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('有任务在运行，等它结束再删')
    wrapper.unmount()
  })
})

describe('FG.5 · 产出文件：列表 / 预览开标签 / 下载', () => {
  async function mountWithTask(): Promise<VueWrapper> {
    const panel = mountPanel()
    useTasksStore().upsert({
      agent_task_id: 't1',
      hadoop_task_id: 'job_1',
      name: '清洗任务',
      summary: '',
      status: 'succeeded',
      status_cn: '成功',
      stage: '',
      progress_percent: 100,
      estimated_seconds: null,
      updated_at: 0,
    })
    await flushPromises()
    return panel
  }

  beforeEach(() => {
    handlers.taskFiles = () =>
      jsonResponse({
        ok: true,
        task_id: 't1',
        files: [
          { path: 'scores.json', size: 5, kind: 'json' },
          { path: 'cleaned/ratings.dat', size: 3, kind: 'dat' },
        ],
      })
  })

  it('点「预览」把文件登记为中栏资源标签（taskId 用 hadoop 任务号）', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="file-preview-btn-scores.json"]').trigger('click')
    await flushPromises()

    expect(useCenterTabsStore().isOpen(fileTabKey('job_1', 'scores.json'))).toBe(true)
    expect(wrapper.find('[data-testid="file-preview-panel"]').exists()).toBe(false)
  })

  it('二进制 / 不可预览文件同样开中栏标签（是否可预览由标签内呈现）', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    await wrapper
      .find('[data-testid="file-preview-btn-cleaned/ratings.dat"]')
      .trigger('click')
    await flushPromises()

    expect(
      useCenterTabsStore().isOpen(fileTabKey('job_1', 'cleaned/ratings.dat')),
    ).toBe(true)
  })

  it('文件行显示类别徽标（日志路径归为日志类）', async () => {
    handlers.taskFiles = () =>
      jsonResponse({
        ok: true,
        task_id: 't1',
        files: [
          { path: 'scores.json', size: 5, kind: 'json' },
          { path: 'logs/driver.log', size: 9, kind: 'text' },
        ],
      })
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    const logBadge = wrapper.find('[data-testid="file-category-logs/driver.log"]')
    expect(logBadge.exists()).toBe(true)
    expect(logBadge.text()).toContain('日志')

    const jsonBadge = wrapper.find('[data-testid="file-category-scores.json"]')
    expect(jsonBadge.exists()).toBe(true)
  })

  it('一律用 hadoop_task_id 列举 / 开预览标签 / 下载（而非 agent_task_id）', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).toContain('/api/tasks/job_1/files')
    expect(paths).not.toContain('/api/tasks/t1/files')

    const download = wrapper.find('[data-testid="file-download-scores.json"]')
    expect(download.attributes('href')).toContain('task_id=job_1')

    await wrapper.find('[data-testid="file-preview-btn-scores.json"]').trigger('click')
    await flushPromises()
    expect(useCenterTabsStore().isOpen(fileTabKey('job_1', 'scores.json'))).toBe(true)
  })

  it('任务无 hadoop_task_id：如实提示且不请求产出文件', async () => {
    const panel = mountPanel()
    useTasksStore().upsert({
      agent_task_id: 't2',
      hadoop_task_id: null,
      name: '排队中任务',
      summary: '',
      status: 'queued',
      status_cn: '排队中',
      stage: '',
      progress_percent: 0,
      estimated_seconds: null,
      updated_at: 0,
    })
    await flushPromises()

    await panel.find('[data-testid="files-task-t2"]').trigger('click')
    await flushPromises()

    expect(panel.find('[data-testid="files-no-hadoop"]').exists()).toBe(true)
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).not.toContain('/api/tasks/t2/files')
  })

  it('产出文件报错只在该段显示，不串到其它段', async () => {
    handlers.taskFiles = () =>
      jsonResponse({
        ok: false,
        error: { code: 'TASK_NOT_FOUND', message: '任务目录不存在：job_1' },
      })
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="files-error"]').text()).toContain('任务目录不存在')
    expect(wrapper.find('[data-testid="raw-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="published-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="schemes-error"]').exists()).toBe(false)
  })
});

describe('FG.5 · 排版：任务选择器带状态与双编号、文件行两行结构', () => {
  async function mountWithTask(): Promise<VueWrapper> {
    const panel = mountPanel()
    useTasksStore().upsert({
      agent_task_id: 't1',
      hadoop_task_id: 'job_1',
      name: '清洗任务',
      summary: '',
      status: 'succeeded',
      status_cn: '成功',
      stage: '',
      progress_percent: 100,
      estimated_seconds: null,
      updated_at: 0,
    })
    await flushPromises()
    return panel
  }

  beforeEach(() => {
    handlers.taskFiles = () =>
      jsonResponse({
        ok: true,
        task_id: 't1',
        files: [{ path: 'scores.json', size: 5, kind: 'json' }],
      })
  })

  it('任务选择器行显示中文状态徽标与 LT/Hadoop 双编号', async () => {
    const wrapper = await mountWithTask()

    expect(wrapper.find('[data-testid="files-task-status-t1"]').text()).toBe('成功')
    expect(wrapper.find('[data-testid="files-task-agent-t1"]').text()).toContain('t1')
    expect(wrapper.find('[data-testid="files-task-hadoop-t1"]').text()).toContain('job_1')
  })

  it('文件行第一行是路径+大小，第二行是类别/类型与操作', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    const row = wrapper.find('[data-testid="file-row-scores.json"]')
    expect(row.exists()).toBe(true)
    // 第一行：路径 + 大小
    expect(row.text()).toContain('scores.json')
    expect(row.text()).toContain('5 B')
    // 第二行：类别徽标、kind、预览、下载都在行内
    expect(row.find('[data-testid="file-category-scores.json"]').exists()).toBe(true)
    expect(row.text()).toContain('json')
    expect(row.find('[data-testid="file-preview-btn-scores.json"]').exists()).toBe(true)
    expect(row.find('[data-testid="file-download-scores.json"]').exists()).toBe(true)
  })

  it('方案行改为两行结构：名称一行、操作一行（查看/删除仍在）', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        schemes: [
          { path: 'config/cleaning_rules.v1.json' },
          { path: 'config/my_custom.v1.json' },
        ],
      })
    const wrapper = mountPanel()
    await flushPromises()

    const item = wrapper.find('[data-testid="scheme-item-1"]')
    expect(item.classes()).toContain('resource-card--stack')
    expect(item.text()).toContain('my_custom.v1.json')
    expect(item.find('[data-testid="scheme-view-1"]').exists()).toBe(true)
    expect(item.find('[data-testid="scheme-delete-1"]').exists()).toBe(true)
  })
})
