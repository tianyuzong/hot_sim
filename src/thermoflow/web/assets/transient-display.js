"use strict";

(function exposeTransientDisplay(global) {
  const DEFAULT_TOLERANCE_K = 1e-9;

  function finiteNumber(value) {
    if (value == null || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function legendValues(lowK, highK, difference = false) {
    const offset = difference ? 0 : 273.15;
    return {
      values: [lowK, (lowK + highK) / 2, highK].map(value => Number((value - offset).toFixed(9))),
      unit: difference ? "Δ℃" : "℃",
    };
  }

  function diffusionScale(frames, toleranceK = DEFAULT_TOLERANCE_K) {
    if (!Array.isArray(frames) || !frames.length) {
      throw new Error("热扩散视图缺少真实播放帧");
    }
    const tolerance = finiteNumber(toleranceK);
    if (tolerance == null || tolerance <= 0) {
      throw new Error("热扩散数值容差必须为有限正数");
    }
    let high = 0;
    for (const frame of frames) {
      const minimum = finiteNumber(frame?.temperature_min_k);
      const maximum = finiteNumber(frame?.temperature_max_k);
      if (minimum == null || maximum == null || maximum < minimum) {
        throw new Error("播放帧包含无效的温度范围");
      }
      high = Math.max(high, maximum - minimum);
    }
    return { low: 0, high, uniform: high < tolerance, toleranceK: tolerance };
  }

  function diffusionScalars(temperatureK, frameMinimumK) {
    const reference = finiteNumber(frameMinimumK);
    if (!Array.isArray(temperatureK) || reference == null) {
      throw new Error("热扩散视图需要有限温度数组和参考温度");
    }
    return temperatureK.map(value => {
      const temperature = finiteNumber(value);
      if (temperature == null) throw new Error("温度场包含非有限温度");
      return temperature - reference;
    });
  }

  function materialRangeWarnings(plan, result) {
    const minimum = finiteNumber(result?.time_steps?.length
      ? result.temperature_min_over_time_k : result?.temperature_min_k);
    const maximum = finiteNumber(result?.time_steps?.length
      ? result.temperature_max_over_time_k : result?.temperature_max_k);
    if (minimum == null || maximum == null) return [];

    const assignments = Array.isArray(plan?.component_materials)
      ? plan.component_materials.filter(item => item?.material)
      : [];
    const records = assignments.length
      ? assignments.map(item => ({ material: item.material, componentId: item.component_id }))
      : plan?.material ? [{ material: plan.material, componentId: null }] : [];
    const grouped = new Map();
    for (const record of records) {
      const material = record.material;
      const identity = JSON.stringify([
        material.name,
        material.source_version,
        material.valid_temperature_min_k,
        material.valid_temperature_max_k,
      ]);
      if (!grouped.has(identity)) grouped.set(identity, { material, componentIds: [] });
      if (record.componentId) grouped.get(identity).componentIds.push(record.componentId);
    }

    const warnings = [];
    for (const { material, componentIds } of grouped.values()) {
      const validMinimumK = finiteNumber(material.valid_temperature_min_k);
      const validMaximumK = finiteNumber(material.valid_temperature_max_k);
      const missingRange = validMinimumK == null || validMaximumK == null;
      const outsideRange = !missingRange
        && (minimum < validMinimumK || maximum > validMaximumK);
      if (!missingRange && !outsideRange) continue;
      const resultRange = `${minimum.toPrecision(6)}–${maximum.toPrecision(6)} K`;
      const rangeDescription = missingRange
        ? "材料有效温区未提供完整"
        : `材料数据有效温区为 ${validMinimumK.toPrecision(6)}–${validMaximumK.toPrecision(6)} K，`
          + `本次全时段结果温区为 ${resultRange}`;
      warnings.push({
        materialName: material.name || "未命名材料",
        componentIds: [...new Set(componentIds)],
        validMinimumK,
        validMaximumK,
        resultMinimumK: minimum,
        resultMaximumK: maximum,
        message: `${material.name || "未命名材料"}：${rangeDescription}。`
          + "当前求解使用常物性外推，未建模温度相关物性和相变，结果不可直接作为工程结论。",
      });
    }
    return warnings;
  }

  global.ThermoFlowTransient = {
    DEFAULT_TOLERANCE_K,
    legendValues,
    diffusionScale,
    diffusionScalars,
    materialRangeWarnings,
  };
})(window);
