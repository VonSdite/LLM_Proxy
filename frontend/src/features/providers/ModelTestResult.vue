<script setup lang="ts">
import type { ModelTestResult } from '@/api/providers'
import { UiBadge, UiHelpPopover } from '@/components/ui'
defineProps<{ result?: ModelTestResult; loading?: boolean }>()
</script>
<template>
  <span class="actions">
    <UiBadge v-if="loading">测试中</UiBadge>
    <template v-else-if="result">
      <UiBadge :variant="result.available ? 'success' : 'danger'">
        {{ result.available ? '可用' : '失败' }}
      </UiBadge>
      <small v-if="result.available" class="muted number">
        {{ result.first_token_latency_ms == null ? '—' : `${result.first_token_latency_ms} ms` }} ·
        {{ result.tps == null ? '—' : `${result.tps} tok/s` }}
      </small>
      <UiHelpPopover v-if="result.error || result.response_model" label="模型测试详情">
        <p v-if="result.response_model">响应模型：{{ result.response_model }}</p>
        <p v-if="result.error">{{ result.error }}</p>
      </UiHelpPopover>
    </template>
  </span>
</template>
