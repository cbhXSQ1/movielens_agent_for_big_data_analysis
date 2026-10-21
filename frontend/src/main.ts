import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useConnectionStore } from '@/stores/connection'
import '@/styles/global.css'
import '@/styles/shell-v2.css'

const pinia = createPinia()
createApp(App).use(pinia).mount('#app')

// 顶栏连接状态实时化：启动 /health 轮询（应用级副作用，放在引导处以保持组件纯净、
// 便于单元测试）。每对话 SSE 流的开/断另由 conversations store 驱动。
useConnectionStore(pinia).startPolling()
