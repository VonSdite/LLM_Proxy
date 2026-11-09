<script setup lang="ts">
defineProps<{ scope: 'provider' | 'mapping' | 'oauth' }>()
</script>
<template>
  <p>
    {{
      scope === 'mapping' ? '经过此映射' : scope === 'oauth' ? '使用 OAuth 上游' : '使用此 Provider'
    }}
    的请求会把正文中的常见敏感信息替换为本次请求的占位符，响应返回时恢复原值。
  </p>
  <ul>
    <li>
      识别范围：密码赋值、密钥与 Token、连接串凭据、私钥与 SSH 公钥、邮箱、手机号、身份证、银行卡和
      IP 等。
    </li>
    <li>请求头：过滤客户端传来的 Cookie、x-api-key 等凭据；上游认证仍由服务配置提供。</li>
    <li>
      可能误判：版本号被当成 IP，11 位订单号被当成手机号，类似 cookie: 的整行文本也可能被替换。
    </li>
    <li>识别边界：纯字母口令、中文姓名和地址等可能无法识别，不能视为完整匿名化。</li>
    <li v-if="scope === 'mapping'">
      叠加规则：映射或目标 Provider / OAuth 任一处启用即生效，关闭此处不会关闭目标自身的脱敏。
    </li>
    <li v-else>叠加规则：模型映射开启脱敏时，即使此处关闭，经该映射的请求仍会脱敏。</li>
  </ul>
</template>
