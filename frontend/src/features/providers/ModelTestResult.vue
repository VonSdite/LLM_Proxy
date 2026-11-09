<script setup lang="ts">
import type { ModelTestResult } from '@/api/providers'
import { UiBadge, UiHelpPopover } from '@/components/ui'
import { modelTpsHelp, modelTtftHelp } from './modelTestHelp'
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
      <UiHelpPopover label="模型测试详情">
        <ul>
          <template v-if="result.available">
            <li v-for="text in [...modelTtftHelp, ...modelTpsHelp]" :key="text">{{ text }}</li>
          </template>
          <li v-if="result.response_model">响应模型：{{ result.response_model }}</li>
          <li v-if="result.error">错误信息：{{ result.error }}</li>
          <li v-else-if="!result.available">
            本次测试失败，上游未返回详细错误。可检查地址、认证与代理后重试。
          </li>
        </ul>
      </UiHelpPopover>
    </template>
  </span>
</template>
