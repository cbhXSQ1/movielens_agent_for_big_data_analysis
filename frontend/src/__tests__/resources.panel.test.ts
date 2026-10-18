// 阶段 G · FG.1–FG.5：资源面板组件测试。
// 只伪造全局 fetch（假后端），组件→store→api→http 全链真实执行，
// 因而能直接断言真实构造出的 multipart FormData。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import ResourcesPanel from '@/views/panels/ResourcesPanel.vue'
import { useConnectionStore } from '@/stores/connection'
import { useTasksStore } from '@/stores/tasks'

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
  schemeContent: () => Response
  taskFiles: () => Response
  preview: () => Response
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
      case '/api/schemes/content':
        return handlers.schemeContent()
      case '/api/files/preview':
        return handlers.preview()
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
    schemeContent: () =>
      jsonResponse({ ok: true, name: 's.json', path: 'config/s.json', content: {} }),
    taskFiles: () => jsonResponse({ ok: true, task_id: 't1', files: [] }),
    preview: () => jsonResponse({ ok: true, path: 'a.json', previewable: false }),
  }
  installFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
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

describe('FG.4 · 方案文件：上传回显 validation 与内容查看', () => {
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

  it('查看方案完整内容（getSchemeContent）', async () => {
    handlers.schemes = () =>
      jsonResponse({
        ok: true,
        schemes: [{ name: 'scoring_scheme.v1.json', path: 'config/scoring_scheme.v1.json' }],
      })
    handlers.schemeContent = () =>
      jsonResponse({
        ok: true,
        name: 'scoring_scheme.v1.json',
        path: 'config/scoring_scheme.v1.json',
        content: { weights: { popularity: 0.5 } },
      })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="scheme-view-0"]').trigger('click')
    await flushPromises()

    const content = wrapper.find('[data-testid="scheme-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('popularity')
  })
})

describe('FG.5 · 产出文件：预览两分支', () => {
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

  it('可预览：json 展示内容', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    handlers.preview = () =>
      jsonResponse({
        ok: true,
        path: 'scores.json',
        kind: 'json',
        previewable: true,
        content: '{"a":1}',
      })
    await wrapper.find('[data-testid="file-preview-btn-scores.json"]').trigger('click')
    await flushPromises()

    const content = wrapper.find('[data-testid="file-preview-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('"a": 1')
  })

  it('不可预览：提示改用下载', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    handlers.preview = () =>
      jsonResponse({ ok: true, path: 'cleaned/ratings.dat', previewable: false, kind: 'dat' })
    await wrapper
      .find('[data-testid="file-preview-btn-cleaned/ratings.dat"]')
      .trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="file-preview-unavailable"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="file-preview-content"]').exists()).toBe(false)
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

  it('一律用 hadoop_task_id 列举 / 预览 / 下载（而非 agent_task_id）', async () => {
    const wrapper = await mountWithTask()
    await wrapper.find('[data-testid="files-task-t1"]').trigger('click')
    await flushPromises()

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).toContain('/api/tasks/job_1/files')
    expect(paths).not.toContain('/api/tasks/t1/files')

    const download = wrapper.find('[data-testid="file-download-scores.json"]')
    expect(download.attributes('href')).toContain('task_id=job_1')

    handlers.preview = () =>
      jsonResponse({ ok: true, path: 'scores.json', kind: 'json', previewable: true, content: '{}' })
    await wrapper.find('[data-testid="file-preview-btn-scores.json"]').trigger('click')
    await flushPromises()
    const previewCall = fetchMock.mock.calls.find(
      ([url]) => new URL(String(url)).pathname === '/api/files/preview',
    )
    expect(previewCall).toBeTruthy()
    expect(new URL(String(previewCall![0])).searchParams.get('task_id')).toBe('job_1')
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
})
