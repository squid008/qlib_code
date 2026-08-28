interface ModelParamField {
  key: string
  label: string
  placeholder: string
  step?: number
}

// 各模型的超参表单字段定义（占位提示为对应模型的 Qlib/默认值）
export const MODEL_PARAM_FIELDS: Record<string, ModelParamField[]> = {
  lightgbm: [
    { key: 'max_depth', label: '最大深度 max_depth', placeholder: 'Qlib默认 8' },
    { key: 'num_leaves', label: '叶子节点数 num_leaves', placeholder: 'Qlib默认 210' },
    { key: 'min_child_samples', label: '叶子最少样本 min_child_samples', placeholder: 'Qlib默认 20' },
    { key: 'learning_rate', label: '学习率 learning_rate', placeholder: 'Qlib默认 0.0421', step: 0.001 },
    { key: 'n_estimators', label: '树数量 n_estimators', placeholder: '默认 100' },
    { key: 'subsample', label: '子采样 subsample', placeholder: 'Qlib默认 0.8789', step: 0.01 },
    { key: 'colsample_bytree', label: '特征采样 colsample_bytree', placeholder: 'Qlib默认 0.8879', step: 0.01 },
    { key: 'reg_alpha', label: 'L1正则 reg_alpha', placeholder: 'Qlib默认 205.7', step: 0.1 },
    { key: 'reg_lambda', label: 'L2正则 reg_lambda', placeholder: 'Qlib默认 580.98', step: 0.1 },
  ],
  xgboost: [
    { key: 'max_depth', label: '最大深度 max_depth', placeholder: 'XGBoost默认 6' },
    { key: 'learning_rate', label: '学习率 learning_rate', placeholder: 'XGBoost默认 0.3', step: 0.01 },
    { key: 'n_estimators', label: '树数量 n_estimators', placeholder: '默认 100（XGB用early_stopping截断）' },
    { key: 'min_child_weight', label: '最小子节点权重 min_child_weight', placeholder: 'XGBoost默认 1' },
    { key: 'subsample', label: '子采样 subsample', placeholder: 'XGBoost默认 1', step: 0.01 },
    { key: 'colsample_bytree', label: '特征采样 colsample_bytree', placeholder: 'XGBoost默认 1', step: 0.01 },
    { key: 'gamma', label: '分裂最小损失减 gamma', placeholder: 'XGBoost默认 0', step: 0.01 },
    { key: 'reg_alpha', label: 'L1正则 reg_alpha', placeholder: 'XGBoost默认 0', step: 0.1 },
    { key: 'reg_lambda', label: 'L2正则 reg_lambda', placeholder: 'XGBoost默认 1', step: 0.1 },
  ],
}

interface ModelParamsFormProps {
  model: string
  modelParams: Record<string, string | number | null> | null | undefined
  onUpdate: (key: string, v: string) => void
}

// 模型超参表单（LightGBM / XGBoost 可配置且字段不同；留空使用各自默认值；Linear 无树参数）
export default function ModelParamsForm({ model, modelParams, onUpdate }: ModelParamsFormProps) {
  const modelKey = model.toLowerCase()
  const fields = MODEL_PARAM_FIELDS[modelKey]
  if (!fields) return null // Linear 等没有树参数
  return (
    <div className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700">
      <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300 mb-3">
        {model} 模型参数（留空 = {model} 默认值）
      </h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {fields.map((f) => (
          <label className="block" key={f.key}>
            <span className="text-sm text-slate-500">{f.label}</span>
            <input
              type="number"
              step={f.step}
              className="mt-1 w-full border rounded px-2 py-1"
              placeholder={f.placeholder}
              value={modelParams?.[f.key] ?? ''}
              onChange={(e) => onUpdate(f.key, e.target.value)}
            />
          </label>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {modelKey === 'lightgbm'
          ? '提示：深度过深 / 叶子数过多 / 学习率过高易过拟合；留空项自动使用 Qlib 默认值。'
          : 'XGBoost 与 LightGBM 参数名不同（如 min_child_weight 而非 min_child_samples，无 num_leaves）；留空项使用 XGBoost 默认值。'}
        复用历史回测参数时会自动带出这些设置。
      </p>
    </div>
  )
}
