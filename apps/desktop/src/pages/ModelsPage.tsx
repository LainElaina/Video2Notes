import { useMemo, useState } from 'react'
import {
  Bot,
  Check,
  CircleDot,
  Cloud,
  Database,
  KeyRound,
  Link2,
  LoaderCircle,
  LockKeyhole,
  Search,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import type { ModelDefinition } from '../domain'
import { useStudioStore } from '../store'

const capabilityLabels: Record<ModelDefinition['capabilities'][number], string> = {
  text: 'TEXT',
  vision: 'VISION',
  asr: 'ASR',
  ocr: 'OCR',
  json: 'JSON',
  timestamps: 'TIME',
}

export function ModelsPage() {
  const providers = useStudioStore(state => state.providers)
  const models = useStudioStore(state => state.models)
  const roles = useStudioStore(state => state.roles)
  const selectedProviderId = useStudioStore(state => state.selectedProviderId)
  const backend = useStudioStore(state => state.backend)
  const addProvider = useStudioStore(state => state.addProvider)
  const testProvider = useStudioStore(state => state.testProvider)
  const saveProviderSecret = useStudioStore(state => state.saveProviderSecret)
  const deleteProviderSecret = useStudioStore(state => state.deleteProviderSecret)
  const bindRole = useStudioStore(state => state.bindRole)
  const [modelQuery, setModelQuery] = useState('')
  const [secret, setSecret] = useState('')
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [providerDraft, setProviderDraft] = useState({
    id: '',
    name: '',
    kind: 'openai_compatible' as 'openai_compatible' | 'ollama',
    baseUrl: '',
    modelId: '',
    vision: false,
  })

  const provider = providers.find(item => item.id === selectedProviderId) ?? providers[0]
  const providerModels = useMemo(
    () =>
      models.filter(
        model =>
          model.providerId === provider?.id &&
          `${model.label} ${model.modelId}`.toLowerCase().includes(modelQuery.toLowerCase()),
      ),
    [modelQuery, models, provider?.id],
  )

  if (!provider) return null

  return (
    <div className="models-page">
      <section className="provider-card">
        <div className="provider-card-icon">
          {provider.kind === 'local' ? (
            <Database size={22} aria-hidden="true" />
          ) : provider.kind === 'ollama' ? (
            <ServerCog size={22} aria-hidden="true" />
          ) : (
            <Cloud size={22} aria-hidden="true" />
          )}
        </div>
        <div className="provider-card-main">
          <span className="section-kicker">{provider.kind.toUpperCase()}</span>
          <h2>{provider.name}</h2>
          <p>{provider.endpoint}</p>
        </div>
        <div className={`provider-connection connection-${provider.status}`}>
          {provider.status === 'testing' ? (
            <LoaderCircle className="spin" size={15} aria-hidden="true" />
          ) : (
            <CircleDot size={15} aria-hidden="true" />
          )}
          {provider.status === 'testing'
            ? '测试中'
            : provider.status === 'connected'
              ? '连接正常'
              : '尚未连接'}
        </div>
        <button
          className="button button-primary"
          type="button"
          onClick={() => testProvider(provider.id)}
          disabled={provider.status === 'testing'}
        >
          <Link2 size={15} aria-hidden="true" />
          测试连接
        </button>
        {provider.kind !== 'local' && (
          <div className="provider-secret-editor">
            <label>
              <span className="sr-only">Provider 密钥</span>
              <input
                type="password"
                value={secret}
                onChange={event => setSecret(event.target.value)}
                placeholder={
                  provider.credentialState === 'stored-locally'
                    ? '已保存；输入新值可替换'
                    : '只写入 Windows 凭据库'
                }
                autoComplete="new-password"
              />
            </label>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                saveProviderSecret(provider.id, secret)
                setSecret('')
              }}
              disabled={!secret.trim() || backend.mode !== 'real'}
            >
              <KeyRound size={14} aria-hidden="true" />
              保存密钥
            </button>
            {provider.credentialState === 'stored-locally' && (
              <button
                className="button button-quiet"
                type="button"
                onClick={() => deleteProviderSecret(provider.id)}
              >
                删除
              </button>
            )}
          </div>
        )}
        <div className="provider-security">
          <LockKeyhole size={15} aria-hidden="true" />
          <span>
            <strong>
              {provider.credentialState === 'not-needed'
                ? '无需密钥'
                : provider.credentialState === 'stored-locally'
                  ? '密钥已存入 Windows 凭据库'
                  : '尚未配置密钥'}
            </strong>
            应用只读取 credential reference，界面和日志不会返回原值。
          </span>
        </div>
      </section>

      <section className="provider-add-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">PROVIDER REGISTRY</span>
            <h3>添加模型供应商</h3>
            <p>新增一个 OpenAI-compatible 或 Ollama endpoint，并登记首个笔记模型。</p>
          </div>
          <button
            className="button button-secondary"
            type="button"
            onClick={() => setShowAddProvider(value => !value)}
          >
            {showAddProvider ? '收起' : '添加 Provider'}
          </button>
        </div>
        {showAddProvider && (
          <div className="provider-add-grid">
            <label>
              Provider ID
              <input
                value={providerDraft.id}
                onChange={event =>
                  setProviderDraft(value => ({ ...value, id: event.target.value }))
                }
                placeholder="my-provider"
              />
            </label>
            <label>
              显示名称
              <input
                value={providerDraft.name}
                onChange={event =>
                  setProviderDraft(value => ({ ...value, name: event.target.value }))
                }
                placeholder="My local or cloud model"
              />
            </label>
            <label>
              类型
              <select
                value={providerDraft.kind}
                onChange={event =>
                  setProviderDraft(value => ({
                    ...value,
                    kind: event.target.value as 'openai_compatible' | 'ollama',
                  }))
                }
              >
                <option value="openai_compatible">OpenAI-compatible</option>
                <option value="ollama">Ollama</option>
              </select>
            </label>
            <label>
              Base URL
              <input
                value={providerDraft.baseUrl}
                onChange={event =>
                  setProviderDraft(value => ({ ...value, baseUrl: event.target.value }))
                }
                placeholder="http://127.0.0.1:11434/v1"
              />
            </label>
            <label>
              模型 ID
              <input
                value={providerDraft.modelId}
                onChange={event =>
                  setProviderDraft(value => ({ ...value, modelId: event.target.value }))
                }
                placeholder="qwen3:14b"
              />
            </label>
            <label className="provider-vision-check">
              <input
                type="checkbox"
                checked={providerDraft.vision}
                onChange={event =>
                  setProviderDraft(value => ({ ...value, vision: event.target.checked }))
                }
              />
              模型支持图像输入
            </label>
            <button
              className="button button-primary"
              type="button"
              disabled={backend.mode !== 'real'}
              onClick={() => {
                addProvider(providerDraft)
                setShowAddProvider(false)
              }}
            >
              保存到本机注册表
            </button>
          </div>
        )}
      </section>

      <section className="model-catalog">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">CAPABILITY CATALOG</span>
            <h3>可用模型</h3>
          </div>
          <label className="compact-search">
            <Search size={14} aria-hidden="true" />
            <span className="sr-only">搜索模型</span>
            <input
              value={modelQuery}
              onChange={event => setModelQuery(event.target.value)}
              placeholder="搜索模型"
            />
          </label>
        </div>
        <div className="model-table-wrap">
          <table className="model-table">
            <thead>
              <tr>
                <th>模型</th>
                <th>位置</th>
                <th>能力</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {providerModels.map(model => (
                <tr key={model.id}>
                  <td>
                    <strong>{model.label}</strong>
                    <small>{model.modelId}</small>
                  </td>
                  <td>
                    {model.locality === 'local' ? (
                      <span className="locality-local">
                        <Database size={13} aria-hidden="true" />
                        本机
                      </span>
                    ) : (
                      <span>
                        <Cloud size={13} aria-hidden="true" />
                        云端
                      </span>
                    )}
                  </td>
                  <td>
                    <span className="capability-list">
                      {model.capabilities.map(capability => (
                        <span key={capability}>{capabilityLabels[capability]}</span>
                      ))}
                    </span>
                  </td>
                  <td>
                    <span className="model-ready">
                      <Check size={13} aria-hidden="true" />
                      可用
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {providerModels.length === 0 && (
            <div className="model-empty">该 Provider 下没有匹配的模型。</div>
          )}
        </div>
      </section>

      <section className="role-routing">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">ROLE BINDING</span>
            <h3>处理角色</h3>
            <p>每个阶段独立选择模型；不兼容能力在保存前即被拒绝。</p>
          </div>
          <div className="routing-valid">
            <ShieldCheck size={16} aria-hidden="true" />
            {roles.length} 个角色配置有效
          </div>
        </div>
        <div className="role-groups">
          {(['语音', '视觉', '笔记'] as const).map(group => (
            <section className="role-group" key={group}>
              <header>
                <span>{group}</span>
                <small>{roles.filter(role => role.group === group).length} 个角色</small>
              </header>
              {roles
                .filter(role => role.group === group)
                .map(role => {
                  const currentModel = models.find(model => model.id === role.modelId)
                  return (
                    <div className="role-row" key={role.id}>
                      <div className="role-icon">
                        {group === '语音' ? (
                          <Bot size={17} aria-hidden="true" />
                        ) : group === '视觉' ? (
                          <Search size={17} aria-hidden="true" />
                        ) : (
                          <KeyRound size={17} aria-hidden="true" />
                        )}
                      </div>
                      <div className="role-copy">
                        <strong>{role.label}</strong>
                        <small>{role.description}</small>
                      </div>
                      <label className="role-select">
                        <span className="sr-only">为{role.label}选择模型</span>
                        <select
                          value={role.modelId}
                          onChange={event => bindRole(role.id, event.target.value)}
                        >
                          {models.map(model => {
                            const compatible = role.requiredCapabilities.every(capability =>
                              model.capabilities.includes(capability),
                            )
                            return (
                              <option value={model.id} disabled={!compatible} key={model.id}>
                                {model.label}
                                {!compatible ? ' · 能力不匹配' : ''}
                              </option>
                            )
                          })}
                        </select>
                      </label>
                      <span
                        className={`role-locality ${
                          currentModel?.locality === 'local' ? 'is-local' : 'is-cloud'
                        }`}
                      >
                        {currentModel?.locality === 'local' ? 'LOCAL' : 'CLOUD'}
                      </span>
                    </div>
                  )
                })}
            </section>
          ))}
        </div>
        <div className="routing-footnote">
          <TriangleAlert size={15} aria-hidden="true" />
          低置信复核只会处理触发条件命中的片段，不会把整段视频重复提交给多个模型。
        </div>
      </section>
    </div>
  )
}
