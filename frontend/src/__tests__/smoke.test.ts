import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import App from '@/App.vue'
import { API_BASE, DEFAULT_API_BASE, apiUrl } from '@/api/config'

describe('scaffold smoke', () => {
  it('接口基址默认与拼接正确', () => {
    expect(API_BASE).toBe(DEFAULT_API_BASE)
    expect(apiUrl('/health')).toBe(`${DEFAULT_API_BASE}/health`)
    expect(apiUrl('api/chat')).toBe(`${DEFAULT_API_BASE}/api/chat`)
  })

  it('App 可挂载', () => {
    const wrapper = mount(App, {
      global: {
        plugins: [createPinia()],
        stubs: {
          NConfigProvider: { template: '<div><slot /></div>' },
          NGlobalStyle: { template: '<div />' },
          NMessageProvider: { template: '<div><slot /></div>' },
        },
      },
    })
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.text()).toContain('电影数据分析助手')
  })
})
