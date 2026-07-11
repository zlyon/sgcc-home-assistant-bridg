"""Vue状态注入辅助工具，用于读取95598页面数据。

向浏览器注入JavaScript，扫描所有DOM元素查找__vue__属性，
从Vue组件实例中提取结构化数据。
"""

from __future__ import annotations

import json
from typing import Any

from .field_contracts import parser_capture_keys

CORE_WANTED_KEYS = (
    "mixinGetYuEdata",
    "consInfoobj",
    "consInfo",
    "electric",
    "powerData",
    "mothData",
    "tableData",
    "tableData_t",
    "sevenEleList",
    "sevenEleList_t",
    "new_sevenEleList",
    "tariffC",
    "start",
    "end",
    "queryYear",
    "activeName",
    "billNumberList",
    "BillList",
    "billList",
    "billMonth",
    "NewtotalBillProvince",
    "optionalYearArray",
    "selectYear",
    "selectValue",
    "listData",
)

PARSER_MONEY_KEYS = parser_capture_keys()

DIAG_ONLY_MONEY_KEYS = (
    # 泛字段只供 SGCC_DIAG 取证；parser 默认不把它们当余额。
    "balance",
    "bal",
)


SELECTED_VUE_DATA_SCRIPT_TEMPLATE = """
const clone = (value) => {
  try { return JSON.parse(JSON.stringify(value)); } catch (e) { return null; }
};
const wantedKeys = __WANTED_KEYS__;
return Array.from(document.querySelectorAll('*'))
  .map((el, index) => {
    const vm = el.__vue__;
    if (!vm) return null;
    const data = {};
    wantedKeys.forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(vm, key)) {
        data[key] = clone(vm[key]);
      }
    });
    if (!Object.keys(data).length) return null;
    return {
      index,
      tag: el.tagName,
      id: el.id || '',
      className: String(el.className || '').slice(0, 160),
      text: (el.innerText || el.textContent || '').trim().slice(0, 500),
      data
    };
  })
  .filter(Boolean);
"""


def _selected_vue_data_script(include_diag_fields: bool = False) -> str:
    keys = [*CORE_WANTED_KEYS, *PARSER_MONEY_KEYS]
    if include_diag_fields:
        keys.extend(DIAG_ONLY_MONEY_KEYS)
    keys = list(dict.fromkeys(keys))
    return SELECTED_VUE_DATA_SCRIPT_TEMPLATE.replace(
        "__WANTED_KEYS__",
        json.dumps(keys, ensure_ascii=False),
    )


SELECTED_VUE_DATA_SCRIPT = _selected_vue_data_script(include_diag_fields=False)


FULL_VUE_DATA_SCRIPT = """
const limits = arguments[0] || {};
const maxDepth = Number(limits.maxDepth || 6);
const maxArray = Number(limits.maxArray || 30);
const maxKeys = Number(limits.maxKeys || 80);
const maxRootKeys = Number(limits.maxRootKeys || 240);
const maxComponents = Number(limits.maxComponents || 80);
const maxString = Number(limits.maxString || 600);
const maxNodes = Number(limits.maxNodes || 12000);
const maxNodesPerComponent = Number(limits.maxNodesPerComponent || 800);
const maxMillis = Number(limits.maxMillis || 1500);
const startedAt = performance.now();
let totalNodes = 0;
let timeBudgetHit = false;
let nodeBudgetHit = false;
let componentStartNodes = 0;
let componentTruncated = false;
let seenObjects = new WeakSet();

const budgetMarker = () => {
  if (performance.now() - startedAt >= maxMillis) {
    timeBudgetHit = true;
    return '<truncated:time-budget>';
  }
  if (totalNodes >= maxNodes) {
    nodeBudgetHit = true;
    return '<truncated:global-node-budget>';
  }
  if (totalNodes - componentStartNodes >= maxNodesPerComponent) {
    componentTruncated = true;
    return '<truncated:component-node-budget>';
  }
  return '';
};

const safeClone = (value, depth = 0, isRoot = false) => {
  if (value === null || value === undefined) return value === undefined ? null : value;
  if (typeof value === 'string') return value.slice(0, maxString);
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  if (typeof value === 'function' || typeof value === 'symbol') return `<${typeof value}>`;
  const marker = budgetMarker();
  if (marker) return marker;
  if (depth >= maxDepth) {
    componentTruncated = true;
    return '<truncated:max-depth>';
  }
  if (typeof value !== 'object') return String(value).slice(0, maxString);
  if (seenObjects.has(value)) return '<circular-or-shared>';
  totalNodes += 1;
  seenObjects.add(value);
  if (Array.isArray(value)) {
    const out = [];
    const limit = Math.min(value.length, maxArray);
    for (let index = 0; index < limit; index += 1) {
      const itemMarker = budgetMarker();
      if (itemMarker) {
        out.push(itemMarker);
        break;
      }
      try { out.push(safeClone(value[index], depth + 1)); }
      catch (e) { out.push(`<error:${e && e.name ? e.name : 'clone'}>`); }
    }
    if (value.length > maxArray) {
      componentTruncated = true;
      out.push(`<truncated:${value.length - maxArray}-items>`);
    }
    return out;
  }
  const out = {};
  const keys = Object.keys(value);
  const keyLimit = isRoot ? maxRootKeys : maxKeys;
  for (const key of keys.slice(0, keyLimit)) {
    const itemMarker = budgetMarker();
    if (itemMarker) {
      out['<truncated>'] = itemMarker;
      break;
    }
    try { out[key] = safeClone(value[key], depth + 1); }
    catch (e) { out[key] = `<error:${e && e.name ? e.name : 'clone'}>`; }
  }
  if (keys.length > keyLimit) {
    componentTruncated = true;
    out['<truncated-keys>'] = `${keys.length - keyLimit} more keys`;
  }
  return out;
};

const seenVms = new Set();
const result = [];
for (const [index, el] of Array.from(document.querySelectorAll('*')).entries()) {
  if (performance.now() - startedAt >= maxMillis || totalNodes >= maxNodes) break;
  const vm = el.__vue__;
  if (!vm || !vm.$data) continue;
  const uid = vm._uid === undefined ? `dom-${index}` : String(vm._uid);
  if (seenVms.has(uid)) continue;
  seenVms.add(uid);
  componentStartNodes = totalNodes;
  componentTruncated = false;
  seenObjects = new WeakSet();
  const data = safeClone(vm.$data, 0, true);
  if (!data || typeof data !== 'object' || !Object.keys(data).length) continue;
  const route = vm.$route ? {
    name: String(vm.$route.name || '').slice(0, 120),
    path: String(vm.$route.path || '').slice(0, 300),
    fullPath: String(vm.$route.fullPath || '').slice(0, 500),
    params: safeClone(vm.$route.params || {}, 1),
    query: safeClone(vm.$route.query || {}, 1)
  } : null;
  result.push({
    uid,
    index,
    componentName: String(
      (vm.$options && (vm.$options.name || vm.$options._componentTag)) || ''
    ).slice(0, 120),
    route,
    tag: el.tagName,
    id: el.id || '',
    className: String(el.className || '').slice(0, 240),
    data,
    capture: {
      nodes: totalNodes - componentStartNodes,
      elapsedMs: Math.round(performance.now() - startedAt),
      truncated: (
        timeBudgetHit ||
        nodeBudgetHit ||
        componentTruncated
      )
    }
  });
  if (result.length >= maxComponents) break;
}
return result;
"""


DOM_SEMANTIC_SCRIPT = """
const maxItems = Number((arguments[0] || {}).maxItems || 240);
const maxText = Number((arguments[0] || {}).maxText || 240);
const visible = (el) => {
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
};
const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, maxText);
const result = [];
for (const el of Array.from(document.querySelectorAll('label,dt,dd,th,td,span,div,p,strong'))) {
  if (result.length >= maxItems || !visible(el) || el.children.length > 3) continue;
  const text = clean(el.innerText || el.textContent);
  if (!text || text.length > maxText) continue;
  const next = el.nextElementSibling;
  const parentText = clean(el.parentElement ? el.parentElement.innerText : '');
  result.push({
    tag: el.tagName,
    label: text,
    value: next ? clean(next.innerText || next.textContent) : '',
    parentText
  });
}
return result;
"""


STORE_SNAPSHOT_SCRIPT = """
const clone = (value) => {
  try { return JSON.parse(JSON.stringify(value)); } catch (e) { return null; }
};
const root = Array.from(document.querySelectorAll('*'))
  .map((el) => el.__vue__)
  .find((vm) => vm && vm.$store);
if (!root || !root.$store) {
  return { state: {}, getters: {}, url: location.href, route: null };
}
return {
  state: clone(root.$store.state) || {},
  getters: clone(root.$store.getters) || {},
  url: location.href,
  route: root.$route ? clone(root.$route) : null
};
"""


def selected_store_snapshot(driver) -> dict[str, Any]:
    """读取当前页面根 Vue 实例的 Vuex state/getters 快照。"""
    return driver.execute_script(STORE_SNAPSHOT_SCRIPT) or {"state": {}, "getters": {}}


def selected_store_state(driver) -> dict[str, Any]:
    """仅读取当前页面 Vuex $store.state，供只需要 state 的调用方使用。"""
    snapshot = selected_store_snapshot(driver)
    return snapshot.get("state") or {}


def selected_vue_data(driver, include_diag_fields: bool = False) -> list[dict[str, Any]]:
    """执行JS脚本，从当前页面提取Vue状态数据。"""
    return driver.execute_script(_selected_vue_data_script(include_diag_fields)) or []


def selected_vue_debug_data(
    driver,
    *,
    max_depth: int = 6,
    max_array: int = 30,
    max_keys: int = 80,
    max_root_keys: int = 240,
    max_components: int = 80,
    max_string: int = 600,
    max_nodes: int = 12000,
    max_nodes_per_component: int = 800,
    max_millis: int = 1500,
) -> list[dict[str, Any]]:
    """Capture unknown component fields under hard per-run and per-component budgets."""
    return driver.execute_script(FULL_VUE_DATA_SCRIPT, {
        "maxDepth": max_depth,
        "maxArray": max_array,
        "maxKeys": max_keys,
        "maxRootKeys": max_root_keys,
        "maxComponents": max_components,
        "maxString": max_string,
        "maxNodes": max_nodes,
        "maxNodesPerComponent": max_nodes_per_component,
        "maxMillis": max_millis,
    }) or []


def dom_semantic_snapshot(driver, *, max_items: int = 240, max_text: int = 240) -> list[dict[str, Any]]:
    """Capture bounded visible label/value hints, never full page HTML."""
    return driver.execute_script(DOM_SEMANTIC_SCRIPT, {
        "maxItems": max_items,
        "maxText": max_text,
    }) or []
