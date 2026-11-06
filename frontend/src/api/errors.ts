const messages: Record<string, string> = {
  unauthorized: '登录状态已失效，请重新登录',
  'username and password are required': '请填写用户名和密码',
  'invalid username or password': '用户名或密码错误',
  'provider not found': 'Provider 不存在',
  'user not found': '用户不存在',
  'username is required': '请填写用户名',
  'ip address is required': '请填写 IP 地址',
  'invalid ip address': 'IP 地址格式无效',
  'failed to create user or ip already exists': '创建用户失败，用户名或 IP 可能已存在',
  'failed to update user': '更新用户失败',
  'failed to delete user': '删除用户失败',
  'provider api must be a valid absolute url': 'API 地址必须是完整 URL',
  'provider proxy must be a valid absolute url': '代理地址必须是完整 URL',
  'provider api must use http:// or https://': 'Provider API 地址仅支持 http:// 或 https://',
  'model_list must be a list or newline-separated string':
    '模型列表格式无效，请使用数组或按行分隔的文本',
  'model test models must be a non-empty list': '请至少选择一个要测试的模型',
  'model test must use either auth_group or api_key, not both':
    '模型测试时 Auth Group 与 API Key 只能二选一',
  'model test auth_entry_id requires auth_group':
    '模型测试时只有选择了 Auth Group 才能指定 Auth Entry',
  'model test auth_group requires auth_entry_id': '模型测试时请选择一个 Auth Entry',
  'model fetch auth_entry_id requires auth_group':
    '拉取模型时只有选择了 Auth Group 才能指定 Auth Entry',
  'model fetch auth_group requires auth_entry_id': '拉取模型时请选择一个 Auth Entry',
  'auth entry id is required': '请输入 Auth Entry ID',
  'server host is required': '请填写监听 IP',
  'server host must be a valid ip address': '监听 IP 格式无效，请填写合法 IP 地址',
  'server port must be an integer': '监听端口必须是整数',
  'server port must be between 1 and 65535': '监听端口必须在 1 到 65535 之间',
  'log path is required': '请填写日志路径',
  'failed to fetch': '网络请求失败，请检查服务是否运行',
}
const namedErrors: [RegExp, string][] = [
  [/^Provider already exists:\s*(.+)$/i, 'Provider 已存在'],
  [/^Provider not found:\s*(.+)$/i, 'Provider 不存在'],
  [/^Auth entry not found:\s*(.+)$/i, 'Auth Entry 不存在'],
  [/^Duplicate provider name detected:\s*(.+)$/i, 'Provider 名称重复'],
  [/^Duplicate provider model mapping detected:\s*(.+)$/i, 'Provider 模型映射重复'],
  [/^Configuration file not found:\s*(.+)$/i, '配置文件不存在'],
  [/^Log level must be one of:\s*(.+)$/i, '日志级别无效，支持'],
]
function upstreamStatus(code: number) {
  const summaries: Record<number, string> = {
    400: '上游接口请求无效，请检查地址和请求参数',
    401: '上游接口鉴权失败，请检查 API Key 或接口能力',
    403: '上游接口拒绝访问，请确认账号权限',
    404: '未找到上游接口，请检查 API 地址',
    408: '上游接口响应超时，请稍后重试',
    409: '上游接口返回冲突，请检查当前配置',
    422: '上游接口无法处理当前请求，请检查请求参数',
    429: '上游接口触发限流，请稍后重试',
  }
  return `${summaries[code] || (code >= 500 ? '上游接口暂时不可用，请稍后重试' : '上游接口返回异常状态')}（${code}）`
}
function upstreamFailure(raw: string) {
  if (/timeout|timed out/i.test(raw)) return '请求上游接口超时，请检查网络或增大超时时间'
  if (/certificate|ssl/i.test(raw)) return '上游证书校验失败，请检查证书或 Verify SSL 设置'
  if (/proxy/i.test(raw)) return '代理连接失败，请检查代理地址和代理可用性'
  if (
    /nameresolutionerror|getaddrinfo|name or service not known|temporary failure in name resolution/i.test(
      raw,
    )
  )
    return '无法解析上游域名，请检查地址是否正确'
  if (/connection refused/i.test(raw)) return '上游拒绝连接，请检查服务是否可达'
  if (/remotedisconnected|connection reset|eof occurred/i.test(raw))
    return '与上游的连接被中断，请稍后重试'
  return '请求上游接口失败，请检查网络连通性和接口地址'
}
export function localizedError(raw: string) {
  if (!raw.trim()) return '操作失败，请重试'
  const normalized = raw.replace(/\s+/g, ' ').trim()
  if (/[\u4e00-\u9fff]/.test(normalized)) return raw
  if (messages[normalized.toLowerCase()]) return messages[normalized.toLowerCase()]!
  for (const [pattern, summary] of namedErrors) {
    const match = normalized.match(pattern)
    if (match) return `${summary}：${match[1]}`
  }
  const entry = normalized.match(/^Provider entry at index (\d+) must be an object$/i)
  if (entry) return `第 ${Number(entry[1]) + 1} 个 Provider 配置必须是对象`
  const status = normalized.match(/^https?:\/\/\S+ returned (\d{3})$/i)
  let summary: string | undefined
  if (status) summary = upstreamStatus(Number(status[1]))
  else if (/^https?:\/\/\S+ returned no models$/i.test(normalized))
    summary = '上游未返回可用模型列表'
  else if (/^https?:\/\/\S+ returned invalid json:/i.test(normalized))
    summary = '上游返回的数据格式无效，无法解析模型列表'
  else if (/^https?:\/\/\S+ request failed:/i.test(normalized))
    summary = upstreamFailure(normalized)
  return summary ? `${summary}\n原始信息：${raw}` : raw
}
