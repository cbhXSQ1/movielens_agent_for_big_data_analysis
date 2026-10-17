<script setup lang="ts">
import { NButton, NResult } from 'naive-ui'
import { API_BASE } from '@/api/config'
</script>

<template>
  <div class="placeholder">
    <n-result
      status="info"
      title="电影数据分析助手"
      description="前端基建已就绪（Vite + Vue 3 + TypeScript + Naive UI + Pinia）。"
    >
      <template #footer>
        <p>接口基址：{{ API_BASE }}</p>
        <n-button type="primary">占位页</n-button>
      </template>
    </n-result>
  </div>
</template>

<style scoped>
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}
</style>
