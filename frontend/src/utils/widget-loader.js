/**
 * Shared widget loader — reads data-* attributes from the <script> tag
 * and mounts any React component into a target container.
 *
 * If no data-api-key is provided, the loader calls /api/widget/init on the
 * API server to auto-discover the key from the page's Origin header.
 *
 * Usage (in each entry point):
 *   import { mountWidget } from './utils/widget-loader';
 *   import MyComponent from './MyComponent';
 *   mountWidget(MyComponent);
 */
import React from 'react';
import { createRoot } from 'react-dom/client';

export async function mountWidget(Component, scriptSelector) {
  const script =
    document.currentScript ||
    document.querySelector(scriptSelector || 'script[data-api-key], script[data-api-url]');

  const get = (attr, fallback = '') =>
    (script && script.getAttribute(attr)) || fallback;

  let apiKey  = get('data-api-key');
  const agentName = get('data-agent-name', 'AI Assistant');
  const apiUrl    = get('data-api-url');
  const wsUrl     = get('data-ws-url');
  const voice     = get('data-voice');
  const mode      = get('data-mode', 'general');

  // Derive the static server base URL from this script's src so the widget
  // can load VAD model files (worklet + onnx) from the same origin.
  const widgetBaseUrl = script && script.src
    ? new URL(script.src).origin
    : '';

  // Auto-discover API key from the server if not embedded in the script tag.
  if (!apiKey && apiUrl) {
    try {
      const res  = await fetch(`${apiUrl}/api/widget/init`);
      const json = await res.json();
      apiKey = json.api_key || '';
      if (apiKey) {
        console.log('[widget] API key auto-discovered for this origin');
      } else {
        console.warn('[widget] /api/widget/init returned no key:', json);
      }
    } catch (e) {
      console.warn('[widget] Failed to auto-discover API key:', e);
    }
  }

  // Use data-target if provided, otherwise create a floating container
  const targetId = get('data-target');
  let container = targetId ? document.getElementById(targetId) : null;
  if (!container) {
    container = document.createElement('div');
    container.id = targetId || `__widget-root-${Date.now()}__`;
    document.body.appendChild(container);
  }

  createRoot(container).render(
    React.createElement(Component, { apiKey, agentName, apiUrl, wsUrl, voice, mode, widgetBaseUrl })
  );
}
