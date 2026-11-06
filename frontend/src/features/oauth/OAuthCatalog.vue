<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { oauthApi } from '@/api/oauth'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { copyText } from '@/features/shared/format'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiEmptyState,
  UiHelpPopover,
  UiIconButton,
  UiInput,
} from '@/components/ui'
const props = defineProps<{ image?: boolean }>()
const request = useRequest((signal) => oauthApi.models(props.image, signal))
const action = useAction(request.refresh)
const clipboard = useAction()
const model = ref('')
onMounted(request.refresh)
async function add() {
  if (!model.value.trim()) return
  if (
    await action.run(
      (signal) => oauthApi.addModel(model.value.trim(), props.image, signal),
      '已添加模型',
    )
  )
    model.value = ''
}
</script>
<template>
  <section class="oauth-catalog surface stack">
    <div class="toolbar">
      <div class="actions">
        <h2>{{ image ? '图片模型' : '文本模型' }}</h2>
        <UiHelpPopover :label="image ? '图片模型说明' : '文本模型说明'">
          <p v-if="image">
            图片接口未传模型时使用默认模型；Codex Responses
            的图片工具也使用此默认值。仍可手动指定目录中的其他图片模型。
          </p>
          <p v-else>
            下游直接使用这里的模型 ID，无需 Provider 前缀。支持 Chat Completions、Responses 和
            Messages 接口。
          </p>
        </UiHelpPopover>
      </div>
      <UiButton
        size="sm"
        icon="reset"
        :disabled="action.busy.value"
        @click="action.run((signal) => oauthApi.restoreModels(image, signal), '已补回内置模型')"
      >
        补回内置
      </UiButton>
    </div>
    <form class="catalog-add" @submit.prevent="add">
      <UiInput
        v-model="model"
        placeholder="添加模型 ID"
        :aria-label="image ? '新图片模型 ID' : '新文本模型 ID'"
        mono
        :disabled="action.busy.value"
      />
      <UiButton type="submit" size="sm" :loading="action.busy.value" :disabled="!model.trim()">
        添加
      </UiButton>
    </form>
    <UiAlert v-if="request.error.value || action.error.value || clipboard.error.value">
      {{ action.error.value || clipboard.error.value || request.error.value }}
    </UiAlert>
    <div class="catalog-models">
      <div v-for="item in request.data.value?.models" :key="item.id" class="catalog-model">
        <code>{{ item.id }}</code>
        <UiBadge v-if="item.built_in">内置</UiBadge>
        <template v-if="image">
          <UiBadge v-if="request.data.value?.default_model === item.id" variant="info">
            默认
          </UiBadge>
          <UiButton
            v-else
            size="sm"
            variant="ghost"
            :disabled="action.busy.value"
            @click="
              action.run(
                (signal) => oauthApi.defaultImageModel(item.id, signal),
                '已设置默认图片模型',
              )
            "
          >
            设为默认
          </UiButton>
        </template>
        <UiIconButton
          icon="copy"
          :label="`复制 ${item.id}`"
          @click="clipboard.run(() => copyText(item.id), '已复制模型 ID')"
        />
        <UiConfirmPopover
          :message="`从目录删除 ${item.id}？`"
          @confirm="
            action.run((signal) => oauthApi.removeModel(item.id, image, signal), '已删除模型')
          "
        >
          <UiIconButton icon="delete" :label="`删除 ${item.id}`" :disabled="action.busy.value" />
        </UiConfirmPopover>
      </div>
      <UiEmptyState
        v-if="!request.data.value?.models.length"
        :loading="request.loading.value"
        title="还没有模型"
      />
    </div>
  </section>
</template>
<style scoped>
.oauth-catalog {
  padding: var(--space-4);
  align-content: start;
}

.catalog-add {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.catalog-add > input {
  flex: 1;
}

.catalog-models {
  display: grid;
  gap: var(--space-1);
  max-height: 240px;
  overflow: auto;
}

.catalog-model {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.catalog-model code {
  min-width: 0;
  overflow-wrap: anywhere;
  margin-right: auto;
}
</style>
