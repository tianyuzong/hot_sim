/* Presentation of server-owned validation; never changes simulation inputs. */
(function (global) {
  function issuesFromPolicy(policy) {
    const issues = [...(policy?.issues || [])];
    for (const [severity, messages] of [['error', policy?.errors], ['warning', policy?.warnings]]) {
      for (const message of messages || []) {
        if (!issues.some(issue => issue.severity === severity && issue.message === message)) {
          issues.push({severity, message, fields: [], suggestion: severity === 'error'
            ? '请按说明检查对应参数；不确定含义时可打开“使用说明”。' : ''});
        }
      }
    }
    return issues;
  }

  class Feedback {
    constructor(root, locate) {
      this.root = root;
      this.locate = locate;
      this.marked = new Set();
    }

    render(study, {pending = false, failure = '', fields = new Map(), inputIssues = []} = {}) {
      for (const node of this.marked) {
        delete node.dataset.validationState;
        node.removeAttribute('aria-invalid');
        node.removeAttribute('aria-describedby');
      }
      this.marked.clear();
      this.root.hidden = !study?.plan || study.confirmation?.status === 'confirmed';
      if (this.root.hidden) return [];
      const issues = [...inputIssues, ...(pending ? [] : issuesFromPolicy(study.policy))];
      if (failure) issues.unshift({severity: 'error', message: failure,
        suggestion: '你的输入未被自动修改；检查参数或连接后重试。', fields: []});
      const errors = issues.filter(issue => issue.severity === 'error');
      const warnings = issues.filter(issue => issue.severity !== 'error');
      const summary = this.root.querySelector('#validationSummary');
      summary.textContent = errors.length ? `${errors.length} 项冲突需修改；点击“定位”查看对应输入。`
        : pending ? '正在检查当前参数，请稍候…'
        : study.policy ? '未发现阻止确认的参数冲突；仍需核对材料与工况。' : '正在检查参数…';
      this.root.dataset.hasErrors = String(errors.length > 0);
      const addRows = (container, values, severity) => {
        const rows = values.map((issue, index) => {
          const row = document.createElement('div');
          row.className = 'validation-issue';
          row.dataset.severity = severity;
          row.id = `validation-${severity}-${index}`;
          const message = document.createElement('p');
          message.textContent = issue.message;
          row.append(message);
          if (issue.suggestion) {
            const hint = document.createElement('small');
            hint.textContent = `建议：${issue.suggestion}`;
            row.append(hint);
          }
          if (issue.fields?.length) {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = '定位';
            button.setAttribute('aria-label', `定位：${issue.message}`);
            button.addEventListener('click', () => this.locate(issue.fields));
            row.append(button);
          }
          for (const field of issue.fields || []) {
            const node = fields.get(field)?.node;
            if (!node || node.disabled || node.dataset.validationState === 'error') continue;
            node.dataset.validationState = severity;
            if (severity === 'error') node.setAttribute('aria-invalid', 'true');
            node.setAttribute('aria-describedby', row.id);
            this.marked.add(node);
          }
          return row;
        });
        container.replaceChildren(...rows);
      };
      addRows(this.root.querySelector('#validationErrors'), errors, 'error');
      addRows(this.root.querySelector('#validationWarningList'), warnings, 'warning');
      this.root.querySelector('#validationWarnings').hidden = !warnings.length;
      this.root.querySelector('#validationWarningCount').textContent = `${warnings.length} 项风险需核对（不一定阻止求解）`;
      return issues;
    }
  }
  global.ThermoFlowValidation = {Feedback, issuesFromPolicy};
})(window);
