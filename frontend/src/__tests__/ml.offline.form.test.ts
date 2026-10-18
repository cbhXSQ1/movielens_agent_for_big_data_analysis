// FE-ML-4 · 离线工具表单适配 ML 训练参数（契约 §1）。
// 训练类工具经推送的工具表带来 `algorithm`（枚举）/ `k` / `n_components`（整数）/
// `params`（对象）；本组用例锁定表单对这几类参数的渲染、校验与提交载荷。
import { describe, it, expect, beforeEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import OfflineToolForm from '@/components/chat/OfflineToolForm.vue'
import { useToolsStore } from '@/stores/tools'
import type { ToolSpec } from '@/api/types'

interface FormHandle {
  selectTool: (name: string) => void
  setValue: (name: string, value: unknown) => void
  submit: () => void
  getValues: () => Record<string, unknown>
  getError: () => string | null
  getPhase: () => string
}

/** 训练类工具的工具表（参数形状逐字对齐契约 §1，含 description 里列出的可用键）。 */
function mlTools(): ToolSpec[] {
  return [
    {
      name: 'train_classifier',
      description: '训练「某用户对某电影是否高分」的分类模型。',
      parameters: [
        {
          name: 'algorithm',
          type: 'string',
          required: true,
          enum: ['naive_bayes', 'random_forest'],
          description: '分类算法。',
        },
        { name: 'data_version', type: 'string', required: false, description: '数据版本号。' },
        { name: 'tag', type: 'string', required: false, description: '任务标签。' },
        {
          name: 'params',
          type: 'object',
          required: false,
          description:
            '算法超参键值对，未知键会被拒绝。可用键：n_estimators（随机森林树数，10～500，'
            + '默认 120）、max_depth（随机森林最大深度，2～40）、min_samples_leaf（1～200）、'
            + 'threshold（0.05～0.95）、alpha（朴素贝叶斯平滑，0.001～10）。',
        },
      ],
      source: 'mcp',
      long_running: true,
      estimated_seconds: 600,
    },
    {
      name: 'cluster_users',
      description: '对用户做偏好画像聚类。',
      parameters: [
        {
          name: 'algorithm',
          type: 'string',
          required: true,
          enum: ['kmeans', 'hierarchical'],
          description: '聚类算法。',
        },
        { name: 'k', type: 'integer', required: false, minimum: 2, maximum: 50, description: '簇数。' },
        { name: 'tag', type: 'string', required: false, description: '任务标签。' },
        { name: 'params', type: 'object', required: false, description: '算法超参键值对。' },
      ],
      source: 'mcp',
      long_running: true,
      estimated_seconds: 300,
    },
    {
      name: 'reduce_dimensions',
      description: '对用户画像做降维。',
      parameters: [
        {
          name: 'algorithm',
          type: 'string',
          required: true,
          enum: ['pca', 'nmf'],
          description: '降维算法。',
        },
        {
          name: 'n_components',
          type: 'integer',
          required: false,
          minimum: 2,
          maximum: 20,
          description: '目标维度。',
        },
        { name: 'params', type: 'object', required: false, description: '算法超参键值对。' },
      ],
      source: 'mcp',
      long_running: true,
      estimated_seconds: 300,
    },
  ]
}

function mountForm(pinia: Pinia): VueWrapper {
  return mount(OfflineToolForm, { global: { plugins: [pinia] } })
}

function handle(wrapper: VueWrapper): FormHandle {
  return wrapper.vm as unknown as FormHandle
}

function submitPayload(wrapper: VueWrapper): Record<string, unknown> {
  const events = wrapper.emitted('submit')
  expect(events, 'submit 未触发').toBeTruthy()
  const first = (events as unknown[][])[0][0] as { params: Record<string, unknown> }
  return first.params
}

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

describe('FE-ML-4 训练类工具：algorithm 枚举 + params 键值编辑器', () => {
  it('train_classifier 渲染 algorithm 枚举控件与 params 键值编辑器', async () => {
    useToolsStore().applySnapshot(mlTools())
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('train_classifier')
    await nextTick()

    const algorithm = wrapper.find('[data-testid="offline-param-algorithm"]')
    expect(algorithm.exists()).toBe(true)
    expect(algorithm.attributes('data-param-type')).toBe('enum')
    expect(wrapper.find('[data-testid="offline-param-algorithm-opt-naive_bayes"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-param-algorithm-opt-random_forest"]').exists()).toBe(true)

    expect(wrapper.find('[data-testid="offline-param-params-editor"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-param-params-add"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-param-params-row-0-key"] input').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-param-params-row-0-value"] input').exists()).toBe(true)

    // 软提示：可用键来自工具表 description（不硬拦未知键）。
    const hint = wrapper.find('[data-testid="offline-param-params-hint"]').text()
    expect(hint).toContain('n_estimators')
  })

  it('填写 algorithm 与 params 后提交，载荷为 {algorithm, params:{...}}', async () => {
    useToolsStore().applySnapshot(mlTools())
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('train_classifier')
    await nextTick()

    handle(wrapper).setValue('algorithm', 'naive_bayes')
    await wrapper.find('[data-testid="offline-param-params-row-0-key"] input').setValue('n_estimators')
    await wrapper.find('[data-testid="offline-param-params-row-0-value"] input').setValue('200')
    await nextTick()

    handle(wrapper).submit()
    await nextTick()

    expect(handle(wrapper).getError()).toBeNull()
    expect(submitPayload(wrapper)).toEqual({
      algorithm: 'naive_bayes',
      params: { n_estimators: 200 },
    })
  })

  it('params 允许多行，且未知键不被硬拦（原样提交）', async () => {
    useToolsStore().applySnapshot(mlTools())
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('train_classifier')
    await nextTick()

    handle(wrapper).setValue('algorithm', 'random_forest')
    await wrapper.find('[data-testid="offline-param-params-row-0-key"] input').setValue('threshold')
    await wrapper.find('[data-testid="offline-param-params-row-0-value"] input').setValue('0.6')
    await wrapper.find('[data-testid="offline-param-params-add"]').trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="offline-param-params-row-1-key"] input').setValue('unknown_key')
    await wrapper.find('[data-testid="offline-param-params-row-1-value"] input').setValue('keep')

    handle(wrapper).submit()
    await nextTick()

    expect(handle(wrapper).getError()).toBeNull()
    expect(submitPayload(wrapper)).toEqual({
      algorithm: 'random_forest',
      params: { threshold: 0.6, unknown_key: 'keep' },
    })
  })
})

describe('FE-ML-4 训练类工具：整数上下界', () => {
  it('cluster_users 的 k 必须落在 [2,50]', async () => {
    useToolsStore().applySnapshot(mlTools())
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('cluster_users')
    await nextTick()

    expect(wrapper.find('[data-testid="offline-param-k"]').attributes('data-param-type')).toBe('integer')

    handle(wrapper).setValue('algorithm', 'kmeans')
    handle(wrapper).setValue('k', 1)
    handle(wrapper).submit()
    await nextTick()
    expect(handle(wrapper).getError()).toContain('k')

    handle(wrapper).setValue('k', 51)
    handle(wrapper).submit()
    await nextTick()
    expect(handle(wrapper).getError()).toContain('k')

    handle(wrapper).setValue('k', 8)
    handle(wrapper).submit()
    await nextTick()
    expect(handle(wrapper).getError()).toBeNull()
    expect(submitPayload(wrapper)).toMatchObject({ algorithm: 'kmeans', k: 8 })
  })

  it('reduce_dimensions 的 n_components 必须落在 [2,20]', async () => {
    useToolsStore().applySnapshot(mlTools())
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('reduce_dimensions')
    await nextTick()

    expect(wrapper.find('[data-testid="offline-param-n_components"]').attributes('data-param-type')).toBe('integer')

    handle(wrapper).setValue('algorithm', 'pca')
    handle(wrapper).setValue('n_components', 21)
    handle(wrapper).submit()
    await nextTick()
    expect(handle(wrapper).getError()).toContain('n_components')
  })
})

describe('FE-ML-4 可选参数默认 null 不得以 null 提交', () => {
  // 端侧推送的工具表（stream.py `_param_entry`）会给未声明默认值的可选参数
  // 补 `default: null`。表单必须把 null 视为「未设置」：不预填、提交时省略，
  // 尤其对象参数（`params`，契约类型 object，默认 `{}`）不得以 null / {} 提交。
  function nullDefaultTools(): ToolSpec[] {
    return [
      {
        name: 'train_classifier',
        description: '训练分类模型。',
        parameters: [
          {
            name: 'algorithm',
            type: 'string',
            required: true,
            enum: ['naive_bayes', 'random_forest'],
            description: '分类算法。',
          },
          { name: 'data_version', type: 'string', required: false, default: null },
          { name: 'tag', type: 'string', required: false, default: null },
          { name: 'params', type: 'object', required: false, default: null },
        ],
        source: 'mcp',
        long_running: true,
        estimated_seconds: 600,
      },
    ]
  }

  it('仅填必填项时，null 默认的可选参数在载荷中整体消失（无 null、无空对象）', async () => {
    useToolsStore().applySnapshot(nullDefaultTools())
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('train_classifier')
    await nextTick()

    // 不预填 null 默认值：values 里不应出现这些键。
    const values = handle(wrapper).getValues()
    expect(values).not.toHaveProperty('data_version')
    expect(values).not.toHaveProperty('tag')
    expect(values).not.toHaveProperty('params')

    handle(wrapper).setValue('algorithm', 'naive_bayes')
    handle(wrapper).submit()
    await nextTick()

    expect(handle(wrapper).getError()).toBeNull()
    const payload = submitPayload(wrapper)
    expect(payload).toEqual({ algorithm: 'naive_bayes' })
    expect(payload).not.toHaveProperty('data_version')
    expect(payload).not.toHaveProperty('tag')
    expect(payload).not.toHaveProperty('params')
  })
})

describe('FE-ML-4 既有（非 ML）工具不受影响', () => {
  it('字符串 / 整数 / 布尔 / 枚举参数照常渲染与提交', async () => {
    const spec: ToolSpec = {
      name: 'clean_data',
      description: '清洗数据',
      parameters: [
        { name: 'chunk', type: 'integer', required: true, minimum: 1, maximum: 100, default: 10 },
        { name: 'method', type: 'enum', required: false, enum: ['mean', 'median'], default: 'mean' },
        { name: 'force', type: 'boolean', required: false, default: false },
        { name: 'tag', type: 'string', required: true, description: '实验标签' },
      ],
      source: 'local',
      long_running: false,
      estimated_seconds: null,
    }
    useToolsStore().applySnapshot([spec])
    const wrapper = mountForm(pinia)
    handle(wrapper).selectTool('clean_data')
    await nextTick()

    expect(wrapper.find('[data-testid="offline-param-chunk"]').attributes('data-param-type')).toBe('integer')
    expect(wrapper.find('[data-testid="offline-param-method"]').attributes('data-param-type')).toBe('enum')
    expect(wrapper.find('[data-testid="offline-param-force"]').attributes('data-param-type')).toBe('boolean')
    expect(wrapper.find('[data-testid="offline-param-tag"]').attributes('data-param-type')).toBe('string')

    handle(wrapper).setValue('tag', '实验A')
    handle(wrapper).submit()
    await nextTick()

    expect(handle(wrapper).getError()).toBeNull()
    expect(submitPayload(wrapper)).toEqual({
      chunk: 10,
      method: 'mean',
      force: false,
      tag: '实验A',
    })
  })
})
