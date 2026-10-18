<script setup lang="ts">
// 阶段 D · FD.2：离线工具表单。
// 工具表来自 tools store（`tools_snapshot` 推送 / `GET /api/tools` 兜底）。
// 选中工具后按参数清单（类型 string / integer / boolean / enum …）渲染控件，
// 预填默认值、标注必填与取值范围；校验通过后 emit `submit`，由对话视图经
// chat store → sendByMode(offline) 提交。
//
// 提交生命周期（避免「点了没反应」的观感）：
//   editing   → 正常编辑（工具 chip + 参数表单）
//   submitting→ 已 emit，等待父级回执：按钮 loading + 「已提交…」提示
//   submitted → 收起表单，换成一行紧凑回执摘要 + 「重新选择工具 / 修改参数」
// 父级在 chat.send 落定后调用 `completeSubmit()` / `failSubmit()` 推进状态。
// 切换工具、或切到 LLM 再切回离线（组件重挂载）都会回到 editing，互不串味。
import { computed, ref } from 'vue'
import {
  NButton,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSwitch,
} from 'naive-ui'
import { storeToRefs } from 'pinia'
import { useToolsStore } from '@/stores/tools'
import type { ToolParamEntry } from '@/api/types'

type ControlKind = 'enum' | 'boolean' | 'integer' | 'number' | 'string' | 'object'
type Phase = 'editing' | 'submitting' | 'submitted'
/** 对象参数（如 ML 的 `params`）里的键值行。 */
type KvRow = { key: string; value: string }

const emit = defineEmits<{
  (e: 'submit', payload: { tool: string; params: Record<string, unknown> }): void
}>()

const toolsStore = useToolsStore()
const { tools } = storeToRefs(toolsStore)

const selectedTool = ref<string | null>(null)
const values = ref<Record<string, unknown>>({})
const error = ref<string | null>(null)
// 对象参数的键值行（按参数名分组）；`values[name]` 由这些行实时归并而成。
const objectRows = ref<Record<string, KvRow[]>>({})

// 提交生命周期状态：见文件头注释。
const phase = ref<Phase>('editing')
// 回执摘要依据（提交那一刻的快照，避免编辑态变更影响已提交记录）。
const submittedTool = ref<string | null>(null)
const submittedParams = ref<Record<string, unknown>>({})

const paramEntries = computed<ToolParamEntry[]>(() =>
  selectedTool.value ? toolsStore.parametersOf(selectedTool.value) : [],
)
const selectedSpec = computed(() =>
  selectedTool.value ? toolsStore.get(selectedTool.value) : null,
)

/** 参数字面量：字符串直出，其余走 JSON（对象/数组安全）。 */
function formatParam(value: unknown): string {
  if (value === null || value === undefined) return String(value)
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** 回执里「（参数：task_id=…, n=5）」的紧凑文本。 */
const receiptParams = computed<string>(() => {
  const entries = Object.entries(submittedParams.value)
  if (!entries.length) return '无参数'
  return entries.map(([key, value]) => `${key}=${formatParam(value)}`).join(', ')
})

function controlKind(param: ToolParamEntry): ControlKind {
  if (Array.isArray(param.enum) && param.enum.length > 0) return 'enum'
  const type = (param.type ?? '').toLowerCase()
  if (type === 'boolean' || type === 'bool') return 'boolean'
  if (type === 'integer' || type === 'int') return 'integer'
  if (type === 'number' || type === 'float' || type === 'double') return 'number'
  if (type === 'object') return 'object'
  return 'string'
}

function enumOptions(param: ToolParamEntry): { label: string; value: string | number }[] {
  return (param.enum ?? []).map((option) => ({
    label: String(option),
    value:
      typeof option === 'number' || typeof option === 'string'
        ? option
        : String(option),
  }))
}

function stringValue(name: string): string {
  const value = values.value[name]
  return value === undefined || value === null ? '' : String(value)
}

function numberValue(name: string): number | null {
  const value = values.value[name]
  if (value === undefined || value === null || value === '') return null
  const parsed = Number(value)
  return Number.isNaN(parsed) ? null : parsed
}

function boolValue(name: string): boolean {
  return Boolean(values.value[name])
}

function selectTool(name: string): void {
  selectedTool.value = name
  const defaults = { ...toolsStore.defaultsFor(name) }
  const rows: Record<string, KvRow[]> = {}
  for (const param of toolsStore.parametersOf(name)) {
    if (controlKind(param) === 'object') {
      rows[param.name] = seedObjectRows(defaults[param.name])
      // 对象参数无默认值时也预置空对象，保证提交载荷形状稳定（契约默认 `{}`）。
      if (defaults[param.name] === undefined) defaults[param.name] = {}
    }
  }
  objectRows.value = rows
  values.value = defaults
  error.value = null
  // 重新选工具即回到编辑态（无论此前是否已提交收起）。
  phase.value = 'editing'
}

/** 把对象默认值摊成键值行；无默认值时给一行空行，便于直接编辑。 */
function seedObjectRows(value: unknown): KvRow[] {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const rows = Object.entries(value as Record<string, unknown>).map(([key, val]) => ({
      key,
      value: val === null || val === undefined ? '' : String(val),
    }))
    if (rows.length) return rows
  }
  return [{ key: '', value: '' }]
}

/** 键值行的字符串值按字面量还原类型：真/假、整数、小数，其余按字符串。 */
function coerceObjectValue(raw: string): unknown {
  const text = raw.trim()
  if (text === '') return ''
  if (text === 'true') return true
  if (text === 'false') return false
  if (/^-?\d+$/.test(text)) return Number(text)
  if (/^-?\d*\.\d+$/.test(text)) return Number(text)
  return raw
}

/** 由键值行归并出对象参数值（空键跳过、未知键原样保留，不硬拦）。 */
function rebuildObject(name: string): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const row of objectRows.value[name] ?? []) {
    const key = row.key.trim()
    if (!key) continue
    out[key] = coerceObjectValue(row.value)
  }
  return out
}

function syncObject(name: string): void {
  values.value = { ...values.value, [name]: rebuildObject(name) }
}

function addObjectRow(name: string): void {
  const rows = objectRows.value[name] ?? []
  objectRows.value = { ...objectRows.value, [name]: [...rows, { key: '', value: '' }] }
  syncObject(name)
}

function removeObjectRow(name: string, index: number): void {
  const rows = objectRows.value[name] ?? []
  const next = rows.filter((_, i) => i !== index)
  objectRows.value = { ...objectRows.value, [name]: next.length ? next : [{ key: '', value: '' }] }
  syncObject(name)
}

function setObjectRow(name: string, index: number, patch: Partial<KvRow>): void {
  const rows = objectRows.value[name] ?? []
  const next = rows.map((row, i) => (i === index ? { ...row, ...patch } : row))
  objectRows.value = { ...objectRows.value, [name]: next }
  syncObject(name)
}

function setValue(name: string, value: unknown): void {
  values.value = { ...values.value, [name]: value }
}

function isBlank(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === '' ||
    (typeof value === 'number' && Number.isNaN(value))
  )
}

function validate(): string | null {
  for (const param of paramEntries.value) {
    const value = values.value[param.name]
    if (param.required && isBlank(value)) return `参数「${param.name}」为必填项`
    const kind = controlKind(param)
    if (!isBlank(value) && (kind === 'integer' || kind === 'number')) {
      const num = Number(value)
      if (Number.isNaN(num)) return `参数「${param.name}」必须是数字`
      if (kind === 'integer' && !Number.isInteger(num)) {
        return `参数「${param.name}」必须是整数`
      }
      if (param.minimum !== undefined && num < param.minimum) {
        return `参数「${param.name}」不得小于 ${param.minimum}`
      }
      if (param.maximum !== undefined && num > param.maximum) {
        return `参数「${param.name}」不得大于 ${param.maximum}`
      }
    }
  }
  return null
}

function submit(): void {
  if (phase.value === 'submitting') return
  if (!selectedTool.value) {
    error.value = '请先选择要调用的工具'
    return
  }
  const message = validate()
  if (message) {
    error.value = message
    return
  }
  error.value = null
  const params: Record<string, unknown> = {}
  for (const param of paramEntries.value) {
    const value = values.value[param.name]
    if (value !== undefined) params[param.name] = value
  }
  // 记录回执快照并进入「进行中」，等父级 send 落定后收起或复位。
  submittedTool.value = selectedTool.value
  submittedParams.value = { ...params }
  phase.value = 'submitting'
  emit('submit', { tool: selectedTool.value, params })
}

/** 父级 chat.send 成功后调用：收起表单，显示回执摘要。 */
function completeSubmit(): void {
  if (phase.value === 'submitting') phase.value = 'submitted'
}

/** 父级 chat.send 失败后调用：复位到编辑态并给出错误。 */
function failSubmit(message?: string): void {
  phase.value = 'editing'
  if (message) error.value = message
}

/** 「重新选择工具 / 修改参数」：从回执态恢复展开（保留原工具与参数）。 */
function reopen(): void {
  phase.value = 'editing'
  error.value = null
}

defineExpose({
  selectTool,
  setValue,
  submit,
  completeSubmit,
  failSubmit,
  reopen,
  getValues: () => values.value,
  getSelectedTool: () => selectedTool.value,
  getParams: () => paramEntries.value,
  getError: () => error.value,
  getPhase: () => phase.value,
})
</script>

<template>
  <div class="offline-tool-form" data-testid="offline-tool-form">
    <div
      v-if="phase === 'submitted'"
      class="offline-tool-form__receipt"
      data-testid="offline-receipt"
    >
      <p class="offline-tool-form__receipt-text" data-testid="offline-receipt-text">
        已调用 <code class="offline-tool-form__tool">{{ submittedTool }}</code>
        <span
          class="offline-tool-form__receipt-params"
          data-testid="offline-receipt-params"
        >（参数：{{ receiptParams }}）</span>
      </p>
      <n-button size="small" data-testid="offline-reopen" @click="reopen">
        重新选择工具 / 修改参数
      </n-button>
    </div>

    <template v-else>
      <div class="offline-tool-form__tools" role="listbox" aria-label="可选工具">
        <button
          v-for="tool in tools"
          :key="tool.name"
          type="button"
          class="tool-chip"
          :class="{ 'tool-chip--active': tool.name === selectedTool }"
          :title="tool.description"
          :data-testid="`offline-tool-${tool.name}`"
          @click="selectTool(tool.name)"
        >
          {{ tool.name }}
        </button>
      </div>
      <p v-if="!tools.length" class="offline-tool-form__empty" data-testid="offline-tools-empty">
        工具表为空，等待 Agent 推送。
      </p>

      <div v-if="selectedTool" class="offline-tool-form__params">
        <p v-if="selectedSpec?.description" class="offline-tool-form__desc">
          {{ selectedSpec.description }}
        </p>

        <div
          v-for="param in paramEntries"
          :key="param.name"
          class="param"
          :data-testid="`offline-param-${param.name}`"
          :data-param-type="controlKind(param)"
        >
          <label class="param__label">
            {{ param.name }}<span v-if="param.required" class="param__req">*</span>
          </label>

          <n-radio-group
            v-if="controlKind(param) === 'enum'"
            :value="values[param.name] as string | number | null"
            @update:value="(v: string | number | null) => setValue(param.name, v)"
          >
            <n-radio-button
              v-for="option in enumOptions(param)"
              :key="option.label"
              :value="option.value"
              :data-testid="`offline-param-${param.name}-opt-${option.label}`"
            >
              {{ option.label }}
            </n-radio-button>
          </n-radio-group>

          <n-switch
            v-else-if="controlKind(param) === 'boolean'"
            :value="boolValue(param.name)"
            :data-testid="`offline-param-${param.name}-control`"
            @update:value="(v: boolean) => setValue(param.name, v)"
          />

          <n-input-number
            v-else-if="controlKind(param) === 'integer' || controlKind(param) === 'number'"
            :value="numberValue(param.name)"
            :min="param.minimum"
            :max="param.maximum"
            :precision="controlKind(param) === 'integer' ? 0 : undefined"
            :data-testid="`offline-param-${param.name}-control`"
            @update:value="(v: number | null) => setValue(param.name, v)"
          />

          <div
            v-else-if="controlKind(param) === 'object'"
            class="param__kv"
            :data-testid="`offline-param-${param.name}-editor`"
          >
            <div
              v-for="(row, index) in objectRows[param.name] || []"
              :key="index"
              class="param__kv-row"
            >
              <div
                class="param__kv-cell"
                :data-testid="`offline-param-${param.name}-row-${index}-key`"
              >
                <n-input
                  :value="row.key"
                  placeholder="键"
                  @update:value="(v: string) => setObjectRow(param.name, index, { key: v })"
                />
              </div>
              <div
                class="param__kv-cell"
                :data-testid="`offline-param-${param.name}-row-${index}-value`"
              >
                <n-input
                  :value="row.value"
                  placeholder="值"
                  @update:value="(v: string) => setObjectRow(param.name, index, { value: v })"
                />
              </div>
              <n-button
                size="small"
                :data-testid="`offline-param-${param.name}-remove-${index}`"
                @click="removeObjectRow(param.name, index)"
              >
                删除
              </n-button>
            </div>
            <n-button
              size="small"
              :data-testid="`offline-param-${param.name}-add`"
              @click="addObjectRow(param.name)"
            >
              添加一行
            </n-button>
          </div>

          <n-input
            v-else
            :value="stringValue(param.name)"
            :data-testid="`offline-param-${param.name}-control`"
            @update:value="(v: string) => setValue(param.name, v)"
          />

          <p class="param__hint" :data-testid="`offline-param-${param.name}-hint`">
            <span v-if="param.description">{{ param.description }}</span>
            <span v-if="param.default !== undefined">默认 {{ param.default }}</span>
            <span v-if="param.minimum !== undefined || param.maximum !== undefined">
              范围 [{{ param.minimum ?? '−∞' }}, {{ param.maximum ?? '+∞' }}]
            </span>
            <span v-if="controlKind(param) === 'object'">键值对；未知键会被端侧拒绝，按上方可用键填写。</span>
          </p>
        </div>

        <n-button
          type="primary"
          block
          :loading="phase === 'submitting'"
          :disabled="phase === 'submitting'"
          data-testid="offline-submit"
          @click="submit"
        >
          调用工具
        </n-button>
        <p
          v-if="phase === 'submitting'"
          class="offline-tool-form__pending"
          data-testid="offline-submitting"
        >
          已提交，结果见上方对话…
        </p>
        <p v-if="error" class="offline-tool-form__error" data-testid="offline-form-error">
          {{ error }}
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.offline-tool-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.offline-tool-form__tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 96px;
  overflow: auto;
}
.tool-chip {
  padding: 5px 12px;
  border: 1px solid var(--n-border-color, #e6e9f0);
  border-radius: 999px;
  background: transparent;
  color: var(--n-text-color-2, #4b5563);
  font-size: var(--ml-text-sm, 13px);
  cursor: pointer;
}
.tool-chip--active {
  border-color: var(--n-primary-color, #2f6fed);
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.1));
  color: var(--n-primary-color, #2f6fed);
}
.offline-tool-form__empty,
.offline-tool-form__desc {
  margin: 0;
  color: var(--n-text-color-3, #8a94a6);
  font-size: var(--ml-text-sm, 13px);
}
.offline-tool-form__params {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 4px;
}
.param {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.param__label {
  font-size: var(--ml-text-sm, 13px);
  font-weight: 600;
}
.param__req {
  margin-left: 2px;
  color: var(--n-error-color, #d03050);
}
.param__hint {
  display: flex;
  gap: 8px;
  margin: 0;
  color: var(--n-text-color-3, #8a94a6);
  font-size: var(--ml-text-xs, 12px);
}
.param__kv {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.param__kv-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.param__kv-cell {
  flex: 1 1 0;
  min-width: 0;
}
.offline-tool-form__error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--ml-text-sm, 13px);
}
.offline-tool-form__receipt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--n-border-color, #e6e9f0);
  border-radius: 12px;
  background: var(--n-action-color, #f7f9fc);
}
.offline-tool-form__receipt-text {
  margin: 0;
  min-width: 0;
  color: var(--n-text-color-2, #4b5563);
  font-size: var(--ml-text-sm, 13px);
  overflow-wrap: anywhere;
}
.offline-tool-form__tool {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.1));
  color: var(--n-primary-color, #2f6fed);
  font-family: var(--ml-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}
.offline-tool-form__receipt-params {
  color: var(--n-text-color-3, #8a94a6);
}
.offline-tool-form__pending {
  margin: 0;
  color: var(--n-text-color-3, #8a94a6);
  font-size: var(--ml-text-sm, 13px);
}
</style>
