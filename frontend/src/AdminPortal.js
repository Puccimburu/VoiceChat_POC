import React, { useState, useEffect } from 'react';
import './AdminPortal.css';

const API_BASE = 'http://localhost:5001';
const PLATFORM_URL = process.env.REACT_APP_PLATFORM_URL || 'https://yourplatform.com';
const ADMIN_TOKEN = process.env.REACT_APP_ADMIN_TOKEN || '';

// Attach admin token to every admin-route request (no-op if token not configured).
const adminHeaders = (extra = {}) =>
  ADMIN_TOKEN ? { 'X-Admin-Token': ADMIN_TOKEN, ...extra } : { ...extra };

function AdminPortal() {
  const [customers, setCustomers] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [newKey, setNewKey] = useState(null);       // newly created key to display
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [visibleKeys, setVisibleKeys] = useState({});
  const [form, setForm] = useState({
    customer_name: '',
    customer_email: '',
    db_type: 'mongodb',
    connection_string: 'mongodb://localhost:27017/',
    database: '',
    db_path: '',
    schema_description: ''
  });
  const [error, setError] = useState('');
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);

  const fetchCustomers = async () => {
    try {
      const res = await fetch(`${API_BASE}/admin/customers`, { headers: adminHeaders() });
      const data = await res.json();
      setCustomers(data.customers || []);
      setApiKeys(data.api_keys || []);
    } catch (e) {
      setError('Could not connect to API server (is it running on port 5001?)');
    } finally {
      setLoading(false);
    }
  };

  const fetchDocuments = async () => {
    setDocsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/admin/documents/list`, { headers: adminHeaders() });
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch (e) {
      // non-fatal — don't block the rest of the portal
    } finally {
      setDocsLoading(false);
    }
  };

  useEffect(() => { fetchCustomers(); fetchDocuments(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    e.target.value = '';   // reset input so same file can be re-uploaded
    setUploading(true);
    setUploadResult(null);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch(`${API_BASE}/admin/documents/upload`, { method: 'POST', headers: adminHeaders(), body: fd });
      const data = await res.json();
      setUploadResult(data);
      if (data.success) fetchDocuments();
    } catch (e) {
      setUploadResult({ success: false, message: 'Upload request failed' });
    } finally {
      setUploading(false);
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    setError('');
    setRegistering(true);
    try {
      const db_config = form.db_type === 'sqlite'
        ? { type: 'sqlite', db_path: form.db_path, schema_description: form.schema_description }
        : { type: 'mongodb', connection_string: form.connection_string, database: form.database, schema_description: form.schema_description };

      const res = await fetch(`${API_BASE}/admin/customers/register`, {
        method: 'POST',
        headers: adminHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          customer_name: form.customer_name,
          customer_email: form.customer_email,
          db_config
        })
      });
      const data = await res.json();
      if (!res.ok) { setError(data.error || 'Registration failed'); return; }
      setNewKey(data);
      setForm({ customer_name: '', customer_email: '', db_type: 'mongodb', connection_string: 'mongodb://localhost:27017/', database: '', db_path: '', schema_description: '' });
      fetchCustomers();
    } catch (e) {
      setError('Failed to register customer');
    } finally {
      setRegistering(false);
    }
  };

  const handleRevoke = async (customerId) => {
    if (!window.confirm('Revoke this customer\'s API key?')) return;
    await fetch(`${API_BASE}/admin/customers/${customerId}/revoke`, { method: 'POST', headers: adminHeaders() });
    fetchCustomers();
  };

  const toggleKeyVisibility = (customerId) => {
    setVisibleKeys(prev => ({ ...prev, [customerId]: !prev[customerId] }));
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
  };

  const getKeyForCustomer = (customerId) => {
    return apiKeys.find(k => k.customer_id === customerId);
  };

  const integrationSnippets = (apiKey) => ({
    curl: `curl -X POST http://localhost:5001/api/agent/query \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "What facilities do you have?"}'`,

    js: `// JavaScript / React
const response = await fetch('http://localhost:5001/api/agent/query', {
  method: 'POST',
  headers: {
    'X-API-Key': '${apiKey}',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ query: 'What facilities do you have?' })
});
const data = await response.json();
console.log(data.response);`,

    python: `# Python
import requests

response = requests.post(
    'http://localhost:5001/api/agent/query',
    headers={'X-API-Key': '${apiKey}'},
    json={'query': 'What facilities do you have?'}
)
print(response.json()['response'])`,

    voice: `# Voice-to-voice (audio in, audio out)
curl -X POST http://localhost:5001/api/agent/voice \\
  -H "X-API-Key: ${apiKey}" \\
  -F "audio=@recording.webm"
# Returns: { "transcript": "...", "response": "...", "audio": "<base64 mp3>" }`
  });

  return (
    <div className="admin-portal">
      <div className="admin-header">
        <div className="admin-header-title">
          <i className="fas fa-shield-alt"></i>
          <div>
            <h1>Voice Agent Platform</h1>
            <p>Admin Portal — Manage customers and API keys</p>
          </div>
        </div>
        <div className="admin-header-badge">
          <span className="badge-dot"></span>
          API running on port 5001
        </div>
      </div>

      {error && (
        <div className="admin-error">
          <i className="fas fa-exclamation-triangle"></i> {error}
        </div>
      )}

      {/* Newly created key highlight */}
      {newKey && (
        <div className="new-key-banner">
          <div className="new-key-banner-header">
            <i className="fas fa-check-circle"></i>
            <strong>Customer registered! Share this API key with them — it won't be shown again.</strong>
            <button className="close-btn" onClick={() => setNewKey(null)}>
              <i className="fas fa-times"></i>
            </button>
          </div>
          <div className="new-key-box">
            <span className="new-key-value">{newKey.api_key}</span>
            <button className="copy-btn" onClick={() => copyToClipboard(newKey.api_key)}>
              <i className="fas fa-copy"></i> Copy
            </button>
          </div>
          <p className="new-key-sub">Customer: <strong>{newKey.customer_name}</strong> · ID: {newKey.customer_id}</p>
        </div>
      )}

      <div className="admin-grid">
        {/* LEFT — Register form */}
        <div className="admin-card">
          <h2><i className="fas fa-user-plus"></i> Register New Customer</h2>
          <p className="card-sub">Each customer connects their own database and gets a unique API key.</p>
          <form onSubmit={handleRegister} className="register-form">
            <label>Business Name</label>
            <input
              type="text" placeholder="e.g. al's Gym"
              value={form.customer_name}
              onChange={e => setForm({ ...form, customer_name: e.target.value })}
              required
            />

            <label>Email</label>
            <input
              type="email" placeholder="admin@gym.com"
              value={form.customer_email}
              onChange={e => setForm({ ...form, customer_email: e.target.value })}
              required
            />

            <div className="form-divider">Their Database</div>

            <label>Database Type</label>
            <div className="db-type-toggle">
              <button
                type="button"
                className={`db-type-btn ${form.db_type === 'mongodb' ? 'active' : ''}`}
                onClick={() => setForm({ ...form, db_type: 'mongodb' })}
              >
                <i className="fas fa-leaf"></i> MongoDB
              </button>
              <button
                type="button"
                className={`db-type-btn ${form.db_type === 'sqlite' ? 'active' : ''}`}
                onClick={() => setForm({ ...form, db_type: 'sqlite' })}
              >
                <i className="fas fa-database"></i> SQLite
              </button>
            </div>

            {form.db_type === 'mongodb' ? (
              <>
                <label>Connection String</label>
                <input
                  type="text" placeholder="mongodb://localhost:27017/"
                  value={form.connection_string}
                  onChange={e => setForm({ ...form, connection_string: e.target.value })}
                  required
                />
                <label>Database Name</label>
                <input
                  type="text" placeholder="e.g. GymDB"
                  value={form.database}
                  onChange={e => setForm({ ...form, database: e.target.value })}
                  required
                />
              </>
            ) : (
              <>
                <label>SQLite File Path <span className="optional">(absolute path on server)</span></label>
                <input
                  type="text" placeholder="e.g. /home/user/mysite/db.sqlite3"
                  value={form.db_path}
                  onChange={e => setForm({ ...form, db_path: e.target.value })}
                  required
                />
              </>
            )}

            <label>Schema Description <span className="optional">(optional — helps the AI)</span></label>
            <textarea
              placeholder="e.g. products table: name, price, stock. orders table: customer, items, status..."
              value={form.schema_description}
              onChange={e => setForm({ ...form, schema_description: e.target.value })}
              rows={3}
            />

            <button type="submit" className="register-btn" disabled={registering}>
              {registering
                ? <><i className="fas fa-spinner fa-spin"></i> Registering...</>
                : <><i className="fas fa-key"></i> Generate API Key</>}
            </button>
          </form>
        </div>

        {/* RIGHT — Customer list */}
        <div className="admin-card">
          <h2><i className="fas fa-users"></i> Registered Customers ({customers.length})</h2>
          {loading ? (
            <div className="loading"><i className="fas fa-spinner fa-spin"></i> Loading...</div>
          ) : customers.length === 0 ? (
            <div className="empty-state">
              <i className="fas fa-building"></i>
              <p>No customers yet. Register one on the left.</p>
            </div>
          ) : (
            <div className="customer-list">
              {customers.map(c => {
                const keyDoc = getKeyForCustomer(c.customer_id);
                const isVisible = visibleKeys[c.customer_id];
                return (
                  <div
                    key={c.customer_id}
                    className={`customer-card ${selectedCustomer === c.customer_id ? 'selected' : ''}`}
                    onClick={() => setSelectedCustomer(selectedCustomer === c.customer_id ? null : c.customer_id)}
                  >
                    <div className="customer-card-header">
                      <div className="customer-info">
                        <div className="customer-avatar">
                          {c.name.charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <strong>{c.name}</strong>
                          <span className="customer-email">{c.email}</span>
                        </div>
                      </div>
                      <div className="customer-meta">
                        {keyDoc && (
                          <span className="usage-badge">
                            <i className="fas fa-bolt"></i> {keyDoc.usage_count || 0} calls
                          </span>
                        )}
                        <span className={`status-badge ${keyDoc ? 'active' : 'inactive'}`}>
                          {keyDoc ? 'Active' : 'No Key'}
                        </span>
                        <i className={`fas fa-chevron-${selectedCustomer === c.customer_id ? 'up' : 'down'}`}></i>
                      </div>
                    </div>

                    {selectedCustomer === c.customer_id && keyDoc && (
                      <div className="customer-detail" onClick={e => e.stopPropagation()}>
                        <div className="detail-row">
                          <span className="detail-label">Customer ID</span>
                          <code>{c.customer_id}</code>
                        </div>
                        <div className="detail-row">
                          <span className="detail-label">DB Type</span>
                          <code>{keyDoc.db_config?.type || 'mongodb'}</code>
                        </div>
                        <div className="detail-row">
                          <span className="detail-label">Database</span>
                          <code>{keyDoc.db_config?.database || keyDoc.db_config?.db_path || '—'}</code>
                        </div>
                        <div className="detail-row">
                          <span className="detail-label">Last Used</span>
                          <span>{keyDoc.last_used ? new Date(keyDoc.last_used).toLocaleString() : 'Never'}</span>
                        </div>
                        <div className="detail-row">
                          <span className="detail-label">API Key</span>
                          <div className="key-row">
                            <code className="key-masked">
                              {isVisible ? keyDoc.key : `${keyDoc.key?.slice(0, 8)}${'•'.repeat(20)}`}
                            </code>
                            <button className="icon-btn" onClick={() => toggleKeyVisibility(c.customer_id)}>
                              <i className={`fas fa-eye${isVisible ? '-slash' : ''}`}></i>
                            </button>
                            {isVisible && (
                              <button className="icon-btn" onClick={() => copyToClipboard(keyDoc.key)}>
                                <i className="fas fa-copy"></i>
                              </button>
                            )}
                          </div>
                        </div>

                        {/* Integration snippets */}
                        {isVisible && keyDoc.key && (
                          <IntegrationSnippets apiKey={keyDoc.key} platformUrl={PLATFORM_URL} />
                        )}

                        <button
                          className="revoke-btn"
                          onClick={() => handleRevoke(c.customer_id)}
                        >
                          <i className="fas fa-ban"></i> Revoke Key
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Documents */}
      <div className="admin-card" style={{ marginTop: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <h2 style={{ margin: 0 }}><i className="fas fa-file-pdf"></i> Knowledge Base Documents</h2>
          <label className="register-btn" style={{ margin: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            {uploading
              ? <><i className="fas fa-spinner fa-spin"></i> Uploading...</>
              : <><i className="fas fa-upload"></i> Upload PDF</>}
            <input type="file" accept=".pdf" style={{ display: 'none' }} onChange={handleUpload} disabled={uploading} />
          </label>
        </div>

        {uploadResult && (
          <div className={uploadResult.success ? 'new-key-banner' : 'admin-error'} style={{ marginBottom: '12px' }}>
            {uploadResult.success
              ? <><i className="fas fa-check-circle"></i> {uploadResult.message}</>
              : <><i className="fas fa-exclamation-triangle"></i> {uploadResult.message}</>}
          </div>
        )}

        {docsLoading ? (
          <div className="loading"><i className="fas fa-spinner fa-spin"></i> Loading documents...</div>
        ) : documents.length === 0 ? (
          <div className="empty-state">
            <i className="fas fa-file-pdf"></i>
            <p>No documents uploaded yet. Upload a PDF to enable document Q&amp;A.</p>
          </div>
        ) : (
          <div className="customer-list">
            {documents.map(doc => (
              <div key={doc} className="customer-card" style={{ cursor: 'default' }}>
                <div className="customer-card-header">
                  <div className="customer-info">
                    <div className="customer-avatar" style={{ background: '#1a3a5c', fontSize: '10px' }}>PDF</div>
                    <div>
                      <strong>{doc}</strong>
                      <span className="customer-email">Indexed in vector store</span>
                    </div>
                  </div>
                  <span className="status-badge active">Ready</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* How it works */}
      <div className="how-it-works">
        <h2><i className="fas fa-info-circle"></i> How It Works</h2>
        <div className="steps">
          <div className="step">
            <div className="step-num">1</div>
            <div>
              <strong>Customer registers</strong>
              <p>You add their business name, email, and their MongoDB database connection. They keep their data — you just connect to it.</p>
            </div>
          </div>
          <div className="step-arrow"><i className="fas fa-arrow-right"></i></div>
          <div className="step">
            <div className="step-num">2</div>
            <div>
              <strong>They get an API key</strong>
              <p>A unique <code>va_...</code> key is generated. They add it as a header in their app: <code>X-API-Key: va_...</code></p>
            </div>
          </div>
          <div className="step-arrow"><i className="fas fa-arrow-right"></i></div>
          <div className="step">
            <div className="step-num">3</div>
            <div>
              <strong>Their app calls the API</strong>
              <p>Any frontend — web, mobile, phone system — sends voice or text. The agent queries <em>their</em> database and answers.</p>
            </div>
          </div>
          <div className="step-arrow"><i className="fas fa-arrow-right"></i></div>
          <div className="step">
            <div className="step-num">4</div>
            <div>
              <strong>Instant voice answers</strong>
              <p>They get back natural language text (+ optional audio). No browsing, no web scraping — just their own data.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function IntegrationSnippets({ apiKey, platformUrl }) {
  const [tab, setTab] = useState('curl');
  const wsUrl = platformUrl.replace(/^https/, 'wss').replace(/^http/, 'ws');

  const snippets = {
    curl: `curl -X POST http://localhost:5001/api/agent/query \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "What facilities do you have?"}'`,

    javascript: `const res = await fetch('http://localhost:5001/api/agent/query', {
  method: 'POST',
  headers: {
    'X-API-Key': '${apiKey}',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ query: 'What facilities do you have?' })
});
const { response } = await res.json();
console.log(response);`,

    python: `import requests

r = requests.post(
    'http://localhost:5001/api/agent/query',
    headers={'X-API-Key': '${apiKey}'},
    json={'query': 'What facilities do you have?', 'return_audio': True}
)
data = r.json()
print(data['response'])
# data['audio'] contains base64 MP3 if return_audio=True`,

    voice: `# Voice-to-voice: send audio file, get audio back
curl -X POST http://localhost:5001/api/agent/voice \\
  -H "X-API-Key: ${apiKey}" \\
  -F "audio=@user_recording.webm"

# Response:
# { "transcript": "What classes are available?",
#   "response":   "We have yoga at 7am and swimming at 8am...",
#   "audio":      "<base64 MP3 — play directly>" }`,

    widget: `<!-- Voice widget — floating mic button -->
<div id="voice-agent"></div>
<script
  src="${platformUrl}/widget.js?v=1.0.0"
  data-api-key="${apiKey}"
  data-agent-name="My Assistant"
  data-target="voice-agent"
  data-ws-url="${wsUrl}/ws"
  data-mode="agent"
></script>

<!-- For local testing use:
  src="http://localhost:8080/widget.js"
  data-ws-url="ws://localhost:8080/ws"
-->`,

    chat: `<!-- Chat widget — floating chat bubble (bottom-right) -->
<script
  src="${platformUrl}/chat-widget.js?v=1.0.0"
  data-api-key="${apiKey}"
  data-agent-name="My Assistant"
  data-api-url="${platformUrl}"
></script>

<!-- For local testing use:
  src="http://localhost:8080/chat-widget.js"
  data-api-url="http://localhost:5001"
-->`
  };

  return (
    <div className="snippets" onClick={e => e.stopPropagation()}>
      <div className="snippets-header">
        <i className="fas fa-code"></i> Integration Code
      </div>
      <div className="snippet-tabs">
        {Object.keys(snippets).map(t => (
          <button
            key={t}
            className={`snippet-tab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'javascript' ? 'JavaScript' : t === 'voice' ? 'Voice API' : t === 'widget' ? 'Voice Widget' : t === 'chat' ? 'Chat Widget' : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      <div className="snippet-body">
        <pre>{snippets[tab]}</pre>
        <button
          className="copy-snippet-btn"
          onClick={() => navigator.clipboard.writeText(snippets[tab])}
        >
          <i className="fas fa-copy"></i> Copy
        </button>
      </div>
    </div>
  );
}

export default AdminPortal;
