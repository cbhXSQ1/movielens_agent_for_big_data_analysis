<script setup lang="ts">
import { NConfigProvider, NGlobalStyle, NMessageProvider } from 'naive-ui'
import AppShell from '@/components/AppShell.vue'
import { naiveTheme, themeOverrides } from '@/theme'
</script>

<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="themeOverrides">
    <n-global-style />
    <n-message-provider>
      <AppShell />
    </n-message-provider>
  </n-config-provider>
</template>
