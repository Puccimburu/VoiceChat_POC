/**
 * widget.js — standalone voice widget entry point.
 *
 *   <div id="voice-agent"></div>
 *   <script
 *     src="https://yourplatform.com/widget.js"
 *     data-api-key="va_..."
 *     data-agent-name="My Assistant"
 *     data-ws-url="ws://localhost:8080/ws"
 *     data-mode="agent"
 *     data-target="voice-agent"
 *   ></script>
 */
import { mountWidget } from './utils/widget-loader';
import VoiceWidget from './VoiceWidget';

(function () {
  mountWidget(VoiceWidget, 'script[data-api-key][src*="widget"]');
})();
