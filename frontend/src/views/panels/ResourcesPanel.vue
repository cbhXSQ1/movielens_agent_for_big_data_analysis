<script setup lang="ts">
// 阶段 G（fe-resources）· FG.1–FG.5：左栏③资源面板。
// 五段：原始数据（.dat/.zip 上传）｜清洗后数据（已发布版本）｜方案文件（.json 上传 + 内容查看）｜
// 产出文件（下载/预览）｜模型（迭代二 §3.7 列表/详情/下载）。
// 数据源一律经 useResourcesStore / useModelsStore（直通接口，不经 LLM）。
// 连接就绪后自动拉取「原始/清洗后/方案/模型」四段列表；产出文件在选中任务时才拉取。
import { ref, watch } from 'vue'
import { useConnectionStore } from '@/stores/connection'
import { useModelsStore } from '@/stores/models'
import { useResourcesStore } from '@/stores/resources'
import RawDataSection from '@/components/resources/RawDataSection.vue'
import PublishedDataSection from '@/components/resources/PublishedDataSection.vue'
import SchemeSection from '@/components/resources/SchemeSection.vue'
import TaskFilesSection from '@/components/resources/TaskFilesSection.vue'
import ModelsSection from '@/components/resources/ModelsSection.vue'

type SegmentKey = 'raw' | 'published' | 'schemes' | 'files' | 'models'

const SEGMENTS: Array<{ key: SegmentKey; label: string }> = [
  { key: 'raw', label: '原始数据' },
  { key: 'published', label: '清洗后数据' },
  { key: 'schemes', label: '方案文件' },
  { key: 'files', label: '产出文件' },
  { key: 'models', label: '模型' },
]

const connection = useConnectionStore()
const resources = useResourcesStore()
const models = useModelsStore()
const active = ref<SegmentKey>('raw')
const loadedOnce = ref(false)

async function refreshAll(): Promise<void> {
  await Promise.all([
    resources.loadRaw(),
    resources.loadPublished(),
    resources.loadSchemes(),
    models.loadModels(),
  ])
}

watch(
  () => connection.connected,
  (ready) => {
    if (ready && !loadedOnce.value) {
      loadedOnce.value = true
      void refreshAll()
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="resources-panel" data-testid="panel-resources">
    <nav class="resources-panel__tabs" role="tablist" aria-label="资源分类">
      <button
        v-for="segment in SEGMENTS"
        :key="segment.key"
        type="button"
        role="tab"
        class="resources-panel__tab"
        :class="{ 'resources-panel__tab--active': active === segment.key }"
        :aria-selected="active === segment.key"
        :data-testid="`resources-tab-${segment.key}`"
        @click="active = segment.key"
      >
        {{ segment.label }}
      </button>
    </nav>

    <div class="resources-panel__body">
      <RawDataSection v-show="active === 'raw'" />
      <PublishedDataSection v-show="active === 'published'" />
      <SchemeSection v-show="active === 'schemes'" />
      <TaskFilesSection v-show="active === 'files'" />
      <ModelsSection v-show="active === 'models'" />
    </div>
  </div>
</template>

<style scoped>
.resources-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.resources-panel__tabs {
  display: flex;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--n-border-color, #e6e9f0);
}
.resources-panel__tab {
  flex: 1 1 0;
  padding: 10px 4px;
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--n-text-color-2, #4b5563);
  font-size: var(--ml-text-sm, 13px);
  white-space: nowrap;
  cursor: pointer;
}
.resources-panel__tab:hover {
  color: var(--n-text-color-1, #1f2733);
}
.resources-panel__tab--active {
  border-bottom-color: var(--n-primary-color, #2f6fed);
  color: var(--n-primary-color, #2f6fed);
}
.resources-panel__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
}
</style>
