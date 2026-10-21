<script setup lang="ts">
// 顶部「设置」弹窗（阶段 C · 迭代打磨）。壳层持有开关状态，此处只做呈现。
import { NModal, NCard } from 'naive-ui'
import SettingsForm from './SettingsForm.vue'

const show = defineModel<boolean>('show', { default: false })
</script>

<template>
  <n-modal
    v-model:show="show"
    :mask-closable="true"
    :auto-focus="false"
    transform-origin="center"
  >
    <n-card
      class="settings-modal"
      data-testid="settings-modal"
      role="dialog"
      aria-label="设置 · 大模型供应商"
      :bordered="false"
      title="设置 · 大模型供应商"
      :style="{ width: 'min(560px, 92vw)' }"
    >
      <SettingsForm v-if="show" @close="show = false" />
    </n-card>
  </n-modal>
</template>
