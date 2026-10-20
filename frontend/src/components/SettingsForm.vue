<script setup lang="ts">
// 顶部「设置」弹窗的表单主体（阶段 C · 迭代打磨）。
// 保存 §3.3 的 llm 供应商连接：API 地址（OpenAI 兼容，填到 /v1）/ API Key / 模型名。
// 命名后长期保存在后端；同名保存覆盖旧档案，也可从已保存列表重新载入。
// 是否使用模型由对话里的模式选择决定，这里没有启用开关。
// 默认值来自前端 `.env.local`，发消息时经 `llm` 字段随请求传出（见 chat store）。
// 「测试连接」调用后端 POST /api/llm/test（§3.3 / §9.4）——组件不直接 fetch，走 api 层。
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NSpace,
  NText,
} from 'naive-ui'
import { testLlm } from '@/api/endpoints'
import { useSettingsStore, type LlmSettings } from '@/stores/settings'

const emit = defineEmits<{ (e: 'close'): void }>()

const settings = useSettingsStore()
const form = reactive<LlmSettings>({ ...settings.llm })

const providerName = ref('')
const selectedName = ref('')
const testing = ref(false)
const saving = ref(false)
const removing = ref('')
const result = ref<{ type: 'success' | 'error' | 'info'; text: string } | null>(null)

const canTest = computed(
  () =>
    form.api_base.trim() !== '' &&
    form.api_key.trim() !== '' &&
    form.model.trim() !== '',
)

onMounted(async () => {
  try {
    await settings.loadProviders()
  } catch (err) {
    result.value = {
      type: 'error',
      text: `已保存的供应商读取失败：${errorText(err)}`,
    }
  }
})

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function apply(name: string): void {
  if (!name) return
  if (!settings.applyProvider(name)) {
    result.value = { type: 'error', text: `未找到供应商「${name}」。` }
    return
  }
  Object.assign(form, settings.llm)
  providerName.value = name
  selectedName.value = name
  result.value = { type: 'info', text: `已载入「${name}」。` }
}

function onProviderChange(event: Event): void {
  apply((event.target as HTMLSelectElement).value)
}

async function save(): Promise<void> {
  settings.update({ ...form })
  const name = providerName.value.trim()
  if (!name) {
    settings.save()
    result.value = { type: 'success', text: '已保存，本次会话生效。' }
    return
  }

  saving.value = true
  try {
    await settings.saveProvider(name)
    selectedName.value = name
    result.value = { type: 'success', text: '已保存（长期有效）。' }
  } catch (err) {
    result.value = { type: 'error', text: `保存失败：${errorText(err)}` }
  } finally {
    saving.value = false
  }
}

async function confirmRemove(name: string): Promise<void> {
  if (!window.confirm(`删除供应商「${name}」？`)) return

  removing.value = name
  try {
    await settings.removeProvider(name)
    if (selectedName.value === name) selectedName.value = ''
    result.value = { type: 'info', text: `已删除「${name}」。` }
  } catch (err) {
    result.value = { type: 'error', text: `删除失败：${errorText(err)}` }
  } finally {
    removing.value = ''
  }
}

async function test(): Promise<void> {
  testing.value = true
  result.value = { type: 'info', text: '正在请求后端自检…' }
  try {
    const res = await testLlm({
      enabled: true,
      api_base: form.api_base.trim(),
      api_key: form.api_key.trim(),
      model: form.model.trim(),
      mode: form.mode,
    })
    if (res.ok === false || res.reachable === false) {
      result.value = {
        type: 'error',
        text: `连接失败：${res.error?.message ?? '上游不可达'}`,
      }
    } else {
      const ms =
        typeof res.latency_ms === 'number' ? `，延迟 ${res.latency_ms} ms` : ''
      result.value = { type: 'success', text: `连通成功${ms}。` }
    }
  } catch (err) {
    result.value = {
      type: 'error',
      text: `连接失败：${errorText(err)}`,
    }
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <n-form
    class="settings-form"
    label-placement="top"
    :show-feedback="false"
    data-testid="settings-form"
  >
    <n-form-item label="供应商名称">
      <n-input
        v-model:value="providerName"
        placeholder="如：工作机 / 本地 Ollama"
        data-testid="settings-provider-name"
      />
    </n-form-item>
    <n-text depth="3" class="settings-form__hint">
      填写名称后保存会长期保留；再次用同名保存即覆盖旧档案。
    </n-text>

    <template v-if="settings.providers.length">
      <n-form-item label="已保存的供应商">
        <select
          class="settings-form__select"
          :value="selectedName"
          data-testid="settings-provider-select"
          @change="onProviderChange"
        >
          <option value="">选择已保存的供应商</option>
          <option v-for="item in settings.providers" :key="item.name" :value="item.name">
            {{ item.name }}
          </option>
        </select>
      </n-form-item>
      <div class="settings-form__providers" data-testid="settings-providers">
        <div
          v-for="item in settings.providers"
          :key="item.name"
          class="settings-form__provider"
          data-testid="settings-provider-item"
        >
          <n-button
            text
            type="primary"
            data-testid="settings-provider-apply"
            @click="apply(item.name)"
          >
            {{ item.name }}
          </n-button>
          <n-button
            text
            type="error"
            :loading="removing === item.name"
            data-testid="settings-provider-delete"
            @click="confirmRemove(item.name)"
          >
            删除
          </n-button>
        </div>
      </div>
    </template>

    <n-form-item label="API 地址">
      <n-input
        v-model:value="form.api_base"
        placeholder="https://api.example.com/v1"
        data-testid="settings-api-base"
      />
    </n-form-item>
    <n-text depth="3" class="settings-form__hint">
      OpenAI 兼容端点，填到 <code>/v1</code> 这一级即可（如本地 Ollama：http://localhost:11434/v1）。
    </n-text>

    <n-form-item label="API Key">
      <n-input
        v-model:value="form.api_key"
        type="password"
        show-password-on="click"
        placeholder="sk-…"
        data-testid="settings-api-key"
      />
    </n-form-item>

    <n-form-item label="模型名">
      <n-input
        v-model:value="form.model"
        placeholder="如 deepseek-v4.1-flash / gpt-4o-mini"
        data-testid="settings-model"
      />
    </n-form-item>
    <n-text depth="3" class="settings-form__hint">
      填服务商给的 <b>Model ID</b>（如 deepseek-v4.1-flash），不是展示名（如 “DeepSeek V4.1 Flash”）。
    </n-text>

    <n-text depth="3" class="settings-form__scope-hint">
      这里只保存模型供应商连接。是否使用大模型由对话里的模式选择决定：选“自动”才会调用模型，选“手动”不调用。
      命名保存后长期有效；不填名称时只更新当前会话。
    </n-text>

    <n-alert
      v-if="result"
      :type="result.type"
      :bordered="false"
      class="settings-form__result"
      data-testid="settings-result"
    >
      {{ result.text }}
    </n-alert>

    <n-space justify="space-between" align="center" class="settings-form__actions">
      <n-text depth="3" class="settings-form__hint">
        连接信息来自前端 .env.local（默认值）；不写浏览器存储，命名保存后存到后端。
      </n-text>
      <n-space :size="8">
        <n-button
          :disabled="!canTest"
          :loading="testing"
          data-testid="settings-test"
          @click="test"
        >
          测试连接
        </n-button>
        <n-button
          type="primary"
          :loading="saving"
          data-testid="settings-save"
          @click="save"
        >
          保存
        </n-button>
        <n-button quaternary data-testid="settings-close" @click="emit('close')">
          关闭
        </n-button>
      </n-space>
    </n-space>
  </n-form>
</template>

<style scoped>
.settings-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.settings-form__scope-hint {
  display: block;
  margin: 0 0 8px;
  font-size: var(--ml-text-sm, 13px);
  line-height: 1.6;
}
.settings-form__hint {
  display: block;
  margin: -8px 0 8px;
  font-size: var(--ml-text-sm, 13px);
  line-height: 1.6;
}
.settings-form__result {
  margin: 8px 0 4px;
}
.settings-form__select {
  width: 100%;
  padding: 7px 10px;
  color: inherit;
  font: inherit;
  background: transparent;
  border: 1px solid var(--n-border-color, #d9d9d9);
  border-radius: 3px;
}
.settings-form__providers {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: -4px 0 8px;
}
.settings-form__provider {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.settings-form__actions {
  margin-top: 8px;
}
</style>
