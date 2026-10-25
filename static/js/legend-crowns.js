/* Shared controls for the native CPS forms. No account data or network access. */
(function () {
  'use strict';
  const series = {
    legend: {label: '旧レジェンド', parent: 'legend_clear'},
    true_legend: {label: '真レジェンド', parent: 'true_legend_clear'},
    zero_legend: {label: '零レジェンド', parent: 'zero_legend_clear'}
  };
  const defaults = () => Object.fromEntries(Object.keys(series).map(key => [key, 4]));
  const modeDefaults = () => Object.fromEntries(Object.keys(series).map(key => [key, 'add']));
  const normaliseModes = raw => Object.fromEntries(Object.keys(series).map(key => [key, raw?.[key] === 'set' ? 'set' : 'add']));
  function normalise(raw, modes = {}) {
    return Object.fromEntries(Object.keys(series).map(key => [key,
      Number.isInteger(raw?.[key]) && raw[key] >= (modes[key] === 'set' ? 0 : 1) && raw[key] <= 4 ? raw[key] : 4]));
  }
  function collect(limits, selected, modes = {}, wanted = 'add') {
    return Object.fromEntries(Object.entries(series)
      .filter(([key, value]) => (selected.has(value.parent) || selected.has('all_stage_clear')) && (modes[key] || 'add') === wanted)
      .map(([key]) => [key, normalise(limits, modes)[key]]));
  }
  function modePicker(tab, key, value, onChange) {
    const row = document.createElement('div');
    row.className = 'legend-crown-row';
    row.dataset.crownModeSeries = key;
    const label = document.createElement('label');
    label.htmlFor = `legend-crown-mode-${tab}-${key}`;
    label.textContent = `${series[key].label}の適用方法`;
    const select = document.createElement('select');
    select.id = label.htmlFor;
    select.add(new Option('クリア済みを残して追加', 'add'));
    select.add(new Option('指定した星に揃える', 'set'));
    select.value = value;
    select.addEventListener('change', () => onChange(select.value));
    row.append(label, select);
    return row;
  }
  function syncMode(tab, key, mode) {
    const select = document.getElementById(`legend-crown-picker-${tab}-${key}`);
    if (!select) return;
    select.querySelector('[value="0"]').disabled = mode !== 'set';
    const custom = select.querySelector('[value="custom"]');
    if (custom) custom.disabled = mode === 'set';
    if (mode !== 'set' && select.value === '0') select.value = '1';
  }
  function picker(tab, key, value, onChange, custom = false) {
    const row = document.createElement('div');
    row.className = 'legend-crown-row';
    row.dataset.crownSeries = key;
    const label = document.createElement('label');
    label.htmlFor = `legend-crown-picker-${tab}-${key}`;
    label.textContent = `${series[key].label} 全ステージ`;
    const select = document.createElement('select');
    select.id = label.htmlFor;
    select.setAttribute('aria-label', `${series[key].label}の全ステージのクリア状況`);
    if (custom) select.add(new Option('個別指定', 'custom'));
    const uncleared = new Option('全て未クリア（★0）', '0');
    uncleared.disabled = true;
    select.add(uncleared);
    for (let n = 1; n <= 4; n++) select.add(new Option(
      n === 1 ? '★1まで' : `★${n}まで（★1〜${n}）`, String(n)));
    select.value = String(value);
    select.addEventListener('change', () => {
      if (select.value !== 'custom') onChange(Number(select.value));
    });
    row.append(label, select);
    return row;
  }
  function detectLimit(maps, selected) {
    if (!maps.length) return 'custom';
    for (let n = 1; n <= 4; n++) {
      const expected = new Set();
      for (const map of maps) for (let star = 0; star < Math.min(n, map.crowns); star++)
        map.stages.forEach((_, stage) => expected.add(`${map.id}:${star}:${stage}`));
      if (expected.size === selected.size && [...expected].every(key => selected.has(key))) return n;
    }
    return 'custom';
  }
  window.CPSLegendCrowns = {series, defaults, modeDefaults, normaliseModes, normalise, collect, modePicker, syncMode, picker, detectLimit};
}());
