/* Admin Dashboard — dashboard.js
 * Requires window.ADMIN_CONFIG = { adminPrefix, chatUrl }
 * All XHR calls go to the existing Admin API under adminPrefix.
 * User-supplied strings are always set via textContent (not innerHTML)
 * to prevent stored-XSS from admin-visible API content.
 */
(function () {
    'use strict';

    var P = window.ADMIN_CONFIG.adminPrefix;    // e.g. "/chat/admin"
    var PAGE_SIZE = 50;

    // ----------------------------------------------------------------
    // Tiny DOM helpers
    // ----------------------------------------------------------------

    function qs(sel, ctx) { return (ctx || document).querySelector(sel); }
    function qsa(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

    /** Create an element with optional class and text. */
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    /** Safely set text on element, appending if parent given. */
    function txt(parent, text) {
        var node = document.createTextNode(text);
        if (parent) { parent.appendChild(node); return parent; }
        return node;
    }

    function show(id) { var e = document.getElementById(id); if (e) e.classList.remove('hidden'); }
    function hide(id) { var e = document.getElementById(id); if (e) e.classList.add('hidden'); }

    /** Format ISO date string to locale-friendly display. */
    function fmtDate(iso) {
        if (!iso) return '—';
        try {
            return new Date(iso).toLocaleString(undefined, {
                year: 'numeric', month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit'
            });
        } catch (e) { return iso; }
    }

    /** Truncate string to max chars. */
    function trunc(s, n) {
        if (!s) return '';
        return s.length > n ? s.slice(0, n) + '…' : s;
    }

    // ----------------------------------------------------------------
    // API helpers
    // ----------------------------------------------------------------

    function apiRequest(method, path, body) {
        var opts = {
            method: method,
            credentials: 'include',
            headers: {}
        };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        return fetch(P + path, opts).then(function (r) {
            if (r.status === 204) return null;
            return r.json().then(function (data) {
                if (!r.ok) {
                    var msg = (data && (data.detail || data.message)) || ('HTTP ' + r.status);
                    var err = new Error(msg);
                    err.status = r.status;
                    err.code = data && data.code;
                    err.data = data;
                    throw err;
                }
                return data;
            });
        });
    }

    function apiGet(path)          { return apiRequest('GET',    path); }
    function apiPost(path, body)   { return apiRequest('POST',   path, body); }
    function apiPatch(path, body)  { return apiRequest('PATCH',  path, body); }
    function apiDelete(path)       { return apiRequest('DELETE', path); }

    // ----------------------------------------------------------------
    // Toast notifications
    // ----------------------------------------------------------------

    function showToast(message, type) {
        var container = document.getElementById('toastContainer');
        if (!container) return;
        var t = el('div', 'toast toast-' + (type || 'info'));
        t.textContent = message;
        container.appendChild(t);
        setTimeout(function () {
            t.style.opacity = '0';
            t.style.transition = 'opacity 0.3s';
            setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 320);
        }, 4000);
    }

    // ----------------------------------------------------------------
    // Confirm modal
    // ----------------------------------------------------------------

    var _confirmResolve = null;

    function showConfirm(title, message, okLabel) {
        return new Promise(function (resolve) {
            _confirmResolve = resolve;
            document.getElementById('confirmTitle').textContent = title;
            document.getElementById('confirmMessage').textContent = message;
            document.getElementById('confirmOk').textContent = okLabel || 'Confirm';
            show('confirmModal');
        });
    }

    document.getElementById('confirmCancel').addEventListener('click', function () {
        hide('confirmModal');
        if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
    });

    document.getElementById('confirmOk').addEventListener('click', function () {
        hide('confirmModal');
        if (_confirmResolve) { _confirmResolve(true); _confirmResolve = null; }
    });

    // ----------------------------------------------------------------
    // Tab navigation
    // ----------------------------------------------------------------

    var currentView = 'feedback-queue';

    function initNavigation() {
        var items = qsa('.nav-item');
        items.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var view = btn.dataset.view;
                if (view === currentView) return;
                items.forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                qsa('.view').forEach(function (v) { v.classList.remove('active'); v.classList.add('hidden'); });
                var target = document.getElementById('view-' + view);
                if (target) { target.classList.remove('hidden'); target.classList.add('active'); }
                currentView = view;
                if      (view === 'feedback-queue')    loadFeedbackQueue(_fqState);
                else if (view === 'feedback-reviewed') loadFeedbackReviewed(_frState);
                else if (view === 'feedback-stats')    loadFeedbackStats();
                else if (view === 'kb-browser')        loadKBBrowser(_kbState);
            });
        });
    }

    // ----------------------------------------------------------------
    // Pending badge
    // ----------------------------------------------------------------

    function updatePendingBadge() {
        apiGet('/feedback?status=pending_review&limit=1')
            .then(function (data) {
                var badge = document.getElementById('pendingBadge');
                if (!badge || !data) return;
                var n = data.total || 0;
                badge.textContent = n > 99 ? '99+' : String(n);
                badge.classList.toggle('visible', n > 0);
            })
            .catch(function () { /* non-fatal */ });
    }

    // ================================================================
    // FEEDBACK QUEUE VIEW
    // ================================================================

    var _fqState = { rating: '', tags: '', date_from: '', date_to: '', offset: 0 };

    function renderFeedbackQueueShell() {
        var view = document.getElementById('view-feedback-queue');
        view.innerHTML = '';

        // Section header
        var hdr = el('div', 'section-header');
        var h2 = el('h2', null, 'Feedback Queue'); hdr.appendChild(h2);
        view.appendChild(hdr);

        // Filter bar — queue is always pending_review; no status filter needed
        var fb = el('div', 'filter-bar');
        fb.appendChild(buildSelect('fq-rating', 'Rating',
            [['', 'All'], ['positive', 'Positive'], ['negative', 'Negative']]));
        fb.appendChild(buildTextInput('fq-tags', 'Tags (CSV)', 'e.g. perf,ipc'));
        fb.appendChild(buildDateInput('fq-date-from', 'From'));
        fb.appendChild(buildDateInput('fq-date-to', 'To'));

        var acts = el('div', 'filter-actions');
        var applyBtn = el('button', 'btn-primary', 'Apply');
        applyBtn.style.padding = '7px 14px';
        applyBtn.addEventListener('click', function () {
            _fqState.rating = qs('#fq-rating').value;
            _fqState.tags = qs('#fq-tags').value.trim();
            _fqState.date_from = qs('#fq-date-from').value;
            _fqState.date_to = qs('#fq-date-to').value;
            _fqState.offset = 0;
            loadFeedbackQueue(_fqState);
        });
        var resetBtn = el('button', 'btn-secondary', 'Reset');
        resetBtn.style.padding = '7px 14px';
        resetBtn.addEventListener('click', function () {
            _fqState = { rating: '', tags: '', date_from: '', date_to: '', offset: 0 };
            qsa('#view-feedback-queue select, #view-feedback-queue input').forEach(function (i) { i.value = ''; });
            loadFeedbackQueue(_fqState);
        });
        acts.appendChild(applyBtn); acts.appendChild(resetBtn);
        fb.appendChild(acts);
        view.appendChild(fb);

        // Table wrapper (updated by loadFeedbackQueue)
        var wrap = el('div', 'data-table-wrap'); wrap.id = 'fq-table-wrap'; view.appendChild(wrap);
        // Pagination
        var pg = el('div', 'pagination'); pg.id = 'fq-pagination'; view.appendChild(pg);
    }

    function loadFeedbackQueue(state) {
        var params = new URLSearchParams({ limit: PAGE_SIZE, offset: state.offset, status: 'pending_review' });
        if (state.rating)         params.set('rating', state.rating);
        if (state.tags)           params.set('tags', state.tags);
        if (state.date_from)      params.set('date_from', state.date_from);
        if (state.date_to)        params.set('date_to', state.date_to);

        var wrap = document.getElementById('fq-table-wrap');
        if (wrap) { wrap.innerHTML = ''; wrap.appendChild(buildLoadingRow()); }

        apiGet('/feedback?' + params.toString())
            .then(function (data) {
                renderFeedbackTable(data, 'fq-table-wrap', 'fq-pagination', function (newOffset) {
                    _fqState.offset = newOffset;
                    loadFeedbackQueue(_fqState);
                });
            })
            .catch(function (e) {
                if (wrap) { wrap.innerHTML = ''; wrap.appendChild(buildErrorRow(String(e))); }
            });
    }

    // ================================================================
    // REVIEWED FEEDBACK VIEW
    // ================================================================

    var _frState = { status: 'approved', rating: '', tags: '', date_from: '', date_to: '', offset: 0 };

    function renderFeedbackReviewedShell() {
        var view = document.getElementById('view-feedback-reviewed');
        view.innerHTML = '';

        var hdr = el('div', 'section-header');
        hdr.appendChild(el('h2', null, 'Reviewed Feedback'));
        view.appendChild(hdr);

        var fb = el('div', 'filter-bar');
        // Default to Approved; All shows everything including pending (acceptable edge case)
        fb.appendChild(buildSelect('fr-status', 'Decision',
            [['approved', 'Approved'], ['rejected', 'Rejected'], ['', 'All']]));
        fb.appendChild(buildSelect('fr-rating', 'Rating',
            [['', 'All'], ['positive', 'Positive'], ['negative', 'Negative']]));
        fb.appendChild(buildTextInput('fr-tags', 'Tags (CSV)', 'e.g. perf,ipc'));
        fb.appendChild(buildDateInput('fr-date-from', 'From'));
        fb.appendChild(buildDateInput('fr-date-to', 'To'));

        var acts = el('div', 'filter-actions');
        var applyBtn = el('button', 'btn-primary', 'Apply');
        applyBtn.style.padding = '7px 14px';
        applyBtn.addEventListener('click', function () {
            _frState.status    = qs('#fr-status').value;
            _frState.rating    = qs('#fr-rating').value;
            _frState.tags      = qs('#fr-tags').value.trim();
            _frState.date_from = qs('#fr-date-from').value;
            _frState.date_to   = qs('#fr-date-to').value;
            _frState.offset    = 0;
            loadFeedbackReviewed(_frState);
        });
        var resetBtn = el('button', 'btn-secondary', 'Reset');
        resetBtn.style.padding = '7px 14px';
        resetBtn.addEventListener('click', function () {
            _frState = { status: 'approved', rating: '', tags: '', date_from: '', date_to: '', offset: 0 };
            qs('#fr-status').value = 'approved';
            qsa('#view-feedback-reviewed select:not(#fr-status), #view-feedback-reviewed input').forEach(function (i) { i.value = ''; });
            loadFeedbackReviewed(_frState);
        });
        acts.appendChild(applyBtn); acts.appendChild(resetBtn);
        fb.appendChild(acts);
        view.appendChild(fb);

        var wrap = el('div', 'data-table-wrap'); wrap.id = 'fr-table-wrap'; view.appendChild(wrap);
        var pg = el('div', 'pagination'); pg.id = 'fr-pagination'; view.appendChild(pg);
    }

    function loadFeedbackReviewed(state) {
        var params = new URLSearchParams({ limit: PAGE_SIZE, offset: state.offset });
        if (state.status)    params.set('status', state.status);
        if (state.rating)    params.set('rating', state.rating);
        if (state.tags)      params.set('tags', state.tags);
        if (state.date_from) params.set('date_from', state.date_from);
        if (state.date_to)   params.set('date_to', state.date_to);

        var wrap = document.getElementById('fr-table-wrap');
        if (wrap) { wrap.innerHTML = ''; wrap.appendChild(buildLoadingRow()); }

        apiGet('/feedback?' + params.toString())
            .then(function (data) {
                renderFeedbackTable(data, 'fr-table-wrap', 'fr-pagination', function (newOffset) {
                    _frState.offset = newOffset;
                    loadFeedbackReviewed(_frState);
                });
            })
            .catch(function (e) {
                if (wrap) { wrap.innerHTML = ''; wrap.appendChild(buildErrorRow(String(e))); }
            });
    }

    function renderFeedbackTable(data, wrapId, pgId, onPageChange) {
        var wrap = document.getElementById(wrapId);
        if (!wrap) return;
        wrap.innerHTML = '';

        // The Reviewed view supports hard-deleting rejected entries; the queue does not.
        var isReviewed = wrapId === 'fr-table-wrap';

        var table = el('table', 'data-table');
        var thead = document.createElement('thead');
        var hrow = document.createElement('tr');
        var headers = ['User', 'Rating', 'Status', 'Query', 'Created', 'Tags'];
        if (isReviewed) headers.push('Actions');
        headers.forEach(function (h) {
            var th = el('th'); th.textContent = h; hrow.appendChild(th);
        });
        thead.appendChild(hrow); table.appendChild(thead);

        var tbody = document.createElement('tbody');
        if (!data.items || data.items.length === 0) {
            var erow = document.createElement('tr');
            var etd = document.createElement('td'); etd.colSpan = headers.length;
            var emp = el('div', 'table-empty', 'No feedback entries match the current filters.');
            etd.appendChild(emp); erow.appendChild(etd); tbody.appendChild(erow);
        } else {
            data.items.forEach(function (entry) {
                var row = document.createElement('tr');
                row.className = 'clickable';
                row.title = 'Click to review';
                row.addEventListener('click', function () { openReviewDrawer(entry.id); });

                var tdUser   = el('td'); tdUser.textContent = trunc(entry.user_id, 28);
                var tdRating = el('td'); tdRating.appendChild(makeChip(entry.rating, 'chip-' + entry.rating));
                var tdStatus = el('td');
                tdStatus.appendChild(makeChip(
                    entry.review_status.replace('_', ' '),
                    'chip-' + entry.review_status.replace('_review', '')));
                if (entry.rolled_back_at && !entry.integrated_into_kb_id) {
                    tdStatus.appendChild(makeChip('rolled back', 'chip-rolled-back'));
                }
                var tdQuery  = el('td', 'truncate'); tdQuery.textContent = trunc(entry.query, 60);
                var tdDate   = el('td'); tdDate.textContent = fmtDate(entry.created_at);
                var tdTags   = el('td'); tdTags.appendChild(makeTagList(entry.reviewer_tags || []));

                [tdUser, tdRating, tdStatus, tdQuery, tdDate, tdTags].forEach(function (td) { row.appendChild(td); });

                if (isReviewed) {
                    row.appendChild(buildFeedbackActionsCell(entry, data, wrapId, pgId, onPageChange));
                }

                tbody.appendChild(row);
            });
        }
        table.appendChild(tbody);
        wrap.appendChild(table);

        var pg = document.getElementById(pgId);
        if (pg) renderPagination(pg, data.total, data.offset, data.limit, onPageChange);
    }

    /** Build the "Actions" cell for a Reviewed-table row. Only rejected entries get a Delete button. */
    function buildFeedbackActionsCell(entry, data, wrapId, pgId, onPageChange) {
        var tdActions = el('td');
        if (entry.review_status !== 'rejected') return tdActions;

        var delBtn = el('button', 'btn-danger', 'Delete');
        delBtn.addEventListener('click', function (ev) {
            ev.stopPropagation();
            deleteRejectedEntry(entry, data, wrapId, pgId, onPageChange, delBtn, tdActions);
        });
        tdActions.appendChild(delBtn);
        return tdActions;
    }

    /** Confirm, then hard-delete a rejected entry and optimistically re-render the table. */
    function deleteRejectedEntry(entry, data, wrapId, pgId, onPageChange, delBtn, tdActions) {
        if (!window.confirm('Delete this rejected entry? This cannot be undone.')) return;

        delBtn.disabled = true;
        var existingErr = qs('.row-error', tdActions);
        if (existingErr) existingErr.remove();

        apiDelete('/feedback/' + encodeURIComponent(entry.id))
            .then(function () {
                var idx = data.items.indexOf(entry);
                if (idx !== -1) data.items.splice(idx, 1);
                if (typeof data.total === 'number' && data.total > 0) data.total -= 1;

                if (data.items.length === 0 && data.offset > 0) {
                    onPageChange(Math.max(0, data.offset - data.limit));
                    return;
                }

                renderFeedbackTable(data, wrapId, pgId, onPageChange);
            })
            .catch(function (e) {
                delBtn.disabled = false;
                var errSpan = el('span', 'row-error', String(e && e.message ? e.message : e));
                errSpan.style.color = 'var(--danger, #c0392b)';
                errSpan.style.marginLeft = '8px';
                tdActions.appendChild(errSpan);
            });
    }

    // ================================================================
    // FEEDBACK STATS VIEW
    // ================================================================

    function loadFeedbackStats() {
        var view = document.getElementById('view-feedback-stats');
        view.innerHTML = '';

        var hdr = el('div', 'section-header');
        hdr.appendChild(el('h2', null, 'Feedback Stats'));
        view.appendChild(hdr);

        var loadMsg = el('p', 'text-muted', 'Loading stats…');
        view.appendChild(loadMsg);

        apiGet('/feedback/stats')
            .then(function (stats) {
                view.innerHTML = '';
                var header = el('div', 'section-header');
                header.appendChild(el('h2', null, 'Feedback Stats'));
                view.appendChild(header);
                view.appendChild(buildStatsGrid(stats));
                if (stats.top_tags && stats.top_tags.length > 0) {
                    view.appendChild(buildTagsChart(stats.top_tags));
                }
            })
            .catch(function (e) {
                view.innerHTML = '';
                var errEl = el('p', 'text-muted', 'Failed to load stats: ' + String(e));
                view.appendChild(errEl);
            });
    }

    function buildStatsGrid(stats) {
        var grid = el('div', 'stats-grid');

        var byStatus  = stats.by_status  || {};
        var byRating  = stats.by_rating  || {};

        function addCard(label, value, sub) {
            var card = el('div', 'stat-card');
            card.appendChild(el('div', 'stat-label', label));
            card.appendChild(el('div', 'stat-value', String(value || 0)));
            if (sub) card.appendChild(el('div', 'stat-sub', sub));
            grid.appendChild(card);
        }

        addCard('Total', stats.total);
        addCard('Pending Review', byStatus.pending_review || 0,
            stats.oldest_pending_hours != null
                ? 'Oldest: ' + stats.oldest_pending_hours.toFixed(1) + 'h'
                : null);
        addCard('Approved', byStatus.approved || 0);
        addCard('Rejected', byStatus.rejected || 0);
        addCard('Positive', byRating.positive || 0);
        addCard('Negative', byRating.negative || 0);
        return grid;
    }

    function buildTagsChart(topTags) {
        var card = el('div', 'chart-card');
        card.appendChild(el('h3', null, 'Top Tags'));
        var chart = el('div', 'bar-chart');
        var maxCount = topTags.reduce(function (m, t) { return Math.max(m, t.count); }, 1);
        topTags.slice(0, 10).forEach(function (t) {
            var row = el('div', 'bar-row');
            var label = el('span', 'bar-label', t.tag);
            row.appendChild(label);
            var track = el('div', 'bar-track');
            var fill = el('div', 'bar-fill');
            fill.style.width = Math.round((t.count / maxCount) * 100) + '%';
            track.appendChild(fill); row.appendChild(track);
            row.appendChild(el('span', 'bar-count', String(t.count)));
            chart.appendChild(row);
        });
        card.appendChild(chart);
        return card;
    }

    // ================================================================
    // KB BROWSER VIEW
    // ================================================================

    var _kbState = { source: '', topic: '', tags: '', date_from: '', date_to: '', offset: 0 };

    function renderKBBrowserShell() {
        var view = document.getElementById('view-kb-browser');
        view.innerHTML = '';

        var hdr = el('div', 'section-header');
        hdr.appendChild(el('h2', null, 'KB Browser'));
        view.appendChild(hdr);

        var fb = el('div', 'filter-bar');
        fb.appendChild(buildTextInput('kb-source', 'Source', 'e.g. blog'));
        fb.appendChild(buildTextInput('kb-topic', 'Topic', 'e.g. networking'));
        fb.appendChild(buildTextInput('kb-tags', 'Tags (CSV)', 'e.g. aws,ec2'));
        fb.appendChild(buildDateInput('kb-date-from', 'Published From'));
        fb.appendChild(buildDateInput('kb-date-to', 'Published To'));

        var acts = el('div', 'filter-actions');
        var applyBtn = el('button', 'btn-primary', 'Apply');
        applyBtn.style.padding = '7px 14px';
        applyBtn.addEventListener('click', function () {
            _kbState.source    = qs('#kb-source').value.trim();
            _kbState.topic     = qs('#kb-topic').value.trim();
            _kbState.tags      = qs('#kb-tags').value.trim();
            _kbState.date_from = qs('#kb-date-from').value;
            _kbState.date_to   = qs('#kb-date-to').value;
            _kbState.offset    = 0;
            loadKBBrowser(_kbState);
        });
        var resetBtn = el('button', 'btn-secondary', 'Reset');
        resetBtn.style.padding = '7px 14px';
        resetBtn.addEventListener('click', function () {
            _kbState = { source: '', topic: '', tags: '', date_from: '', date_to: '', offset: 0 };
            qsa('#view-kb-browser select, #view-kb-browser input').forEach(function (i) { i.value = ''; });
            loadKBBrowser(_kbState);
        });
        acts.appendChild(applyBtn); acts.appendChild(resetBtn);
        fb.appendChild(acts); view.appendChild(fb);

        var wrap = el('div', 'data-table-wrap'); wrap.id = 'kb-table-wrap'; view.appendChild(wrap);
        var pg = el('div', 'pagination'); pg.id = 'kb-pagination'; view.appendChild(pg);
    }

    function loadKBBrowser(state) {
        var params = new URLSearchParams({ limit: PAGE_SIZE, offset: state.offset });
        if (state.source)    params.set('source', state.source);
        if (state.topic)     params.set('topic', state.topic);
        if (state.tags)      params.set('tags', state.tags);
        if (state.date_from) params.set('date_from', state.date_from);
        if (state.date_to)   params.set('date_to', state.date_to);

        var wrap = document.getElementById('kb-table-wrap');
        if (wrap) { wrap.innerHTML = ''; wrap.appendChild(buildLoadingRow()); }

        apiGet('/kb/documents?' + params.toString())
            .then(function (data) { renderKBTable(data, state); })
            .catch(function (e) {
                if (wrap) { wrap.innerHTML = ''; wrap.appendChild(buildErrorRow(String(e))); }
            });
    }

    function renderKBTable(data, state) {
        var wrap = document.getElementById('kb-table-wrap');
        if (!wrap) return;
        wrap.innerHTML = '';

        var table = el('table', 'data-table');
        var thead = document.createElement('thead');
        var hrow = document.createElement('tr');
        ['Title / ID', 'Source', 'Topic', 'Tags', 'Chunks', 'Created'].forEach(function (h) {
            var th = el('th'); th.textContent = h; hrow.appendChild(th);
        });
        thead.appendChild(hrow); table.appendChild(thead);

        var tbody = document.createElement('tbody');
        if (!data.items || data.items.length === 0) {
            var erow = document.createElement('tr');
            var etd = document.createElement('td'); etd.colSpan = 6;
            var emp = el('div', 'table-empty', 'No KB documents match the current filters.');
            etd.appendChild(emp); erow.appendChild(etd); tbody.appendChild(erow);
        } else {
            data.items.forEach(function (doc) {
                var row = document.createElement('tr');
                row.className = 'clickable';
                row.title = 'Click to edit';
                row.addEventListener('click', function () { openKBEditor(doc.id); });

                // Title with ID as subtitle
                var tdTitle = document.createElement('td');
                var titleText = el('div'); titleText.textContent = trunc(doc.title || doc.id, 40);
                var idSmall = el('small', 'text-muted text-small'); idSmall.textContent = trunc(doc.id, 50);
                tdTitle.appendChild(titleText); tdTitle.appendChild(idSmall);

                var tdSource = el('td', 'truncate'); tdSource.textContent = doc.source || '—';
                var tdTopic  = el('td', 'truncate'); tdTopic.textContent = doc.topic || '—';
                var tdTags   = el('td'); tdTags.appendChild(makeTagList(doc.tags || []));
                var tdChunks = el('td'); tdChunks.textContent = doc.chunk_count != null ? String(doc.chunk_count) : '—';
                var tdDate   = el('td'); tdDate.textContent = fmtDate(doc.created_at);

                [tdTitle, tdSource, tdTopic, tdTags, tdChunks, tdDate].forEach(function (td) { row.appendChild(td); });
                tbody.appendChild(row);
            });
        }
        table.appendChild(tbody);
        wrap.appendChild(table);

        var pg = document.getElementById('kb-pagination');
        if (pg) renderPagination(pg, data.total, data.offset, data.limit, function (newOffset) {
            _kbState.offset = newOffset;
            loadKBBrowser(_kbState);
        });
    }

    // ================================================================
    // REVIEW DRAWER
    // ================================================================

    function openReviewDrawer(id) {
        var body = document.getElementById('reviewDrawerBody');
        body.innerHTML = '';
        body.appendChild(buildLoadingRow());
        show('reviewDrawer');

        apiGet('/feedback/' + encodeURIComponent(id))
            .then(function (entry) {
                body.innerHTML = '';
                body.appendChild(buildReviewDrawerContent(entry));
            })
            .catch(function (e) {
                body.innerHTML = '';
                body.appendChild(el('p', 'text-muted', 'Failed to load feedback: ' + String(e)));
            });
    }

    document.getElementById('reviewDrawerClose').addEventListener('click', function () { hide('reviewDrawer'); });
    document.getElementById('reviewDrawer').addEventListener('click', function (ev) {
        if (ev.target === this) hide('reviewDrawer');
    });

    function buildReviewDrawerContent(entry) {
        var frag = document.createDocumentFragment();

        // Meta section
        var metaSec = el('div', 'drawer-section');
        metaSec.appendChild(el('h4', null, 'Details'));
        var metaRow = el('div', 'meta-row');
        metaRow.appendChild(metaItem('User', trunc(entry.user_id, 40)));
        metaRow.appendChild(metaItem('Rating', entry.rating));
        metaRow.appendChild(metaItem('Status', entry.review_status));
        metaRow.appendChild(metaItem('Created', fmtDate(entry.created_at)));
        if (entry.model_id) metaRow.appendChild(metaItem('Model', trunc(entry.model_id, 30)));
        metaSec.appendChild(metaRow);

        if (entry.kb_sources_used && entry.kb_sources_used.length > 0) {
            var kbSpan = el('div', 'text-muted text-small');
            kbSpan.textContent = 'KB sources: ' + entry.kb_sources_used.map(function (s) {
                return s.title || s.source || String(s);
            }).join(', ');
            metaSec.appendChild(kbSpan);
        }
        frag.appendChild(metaSec);

        // Full conversation: history context + rated AI response
        var allMessages = (entry.conversation_history || []).slice();
        // Only add query if history is empty (it's already the last user msg in history)
        if (!allMessages.length && entry.query) allMessages.push({role: 'user', content: entry.query});
        if (entry.ai_response) allMessages.push({role: 'assistant', content: entry.ai_response});

        if (allMessages.length > 0) {
            var histDetails = document.createElement('details');
            histDetails.className = 'drawer-section';
            histDetails.setAttribute('open', '');
            var histSummary = document.createElement('summary');
            histSummary.textContent = 'Message History';
            histSummary.className = 'drawer-section-summary';
            histDetails.appendChild(histSummary);
            var histList = el('div', 'history-messages');
            allMessages.forEach(function (msg) {
                var bubble = el('div', 'history-msg history-msg--' + (msg.role || 'user'));
                var roleLabel = el('span', 'history-msg-role', msg.role === 'assistant' ? 'Assistant' : 'User');
                var content = el('div', 'history-msg-content');
                var raw = msg.content || '';
if (window.marked && window.DOMPurify) {
    var html = marked.parse(raw);
    content.innerHTML = DOMPurify.sanitize(html);
} else {
    content.textContent = raw;
}
                bubble.appendChild(roleLabel);
                bubble.appendChild(content);
                histList.appendChild(bubble);
            });
            histDetails.appendChild(histList);
            frag.appendChild(histDetails);
            // Scroll to bottom after DOM renders
            setTimeout(function () { histList.scrollTop = histList.scrollHeight; }, 0);
        }

        // Content section: correction / user comment
        if (entry.correction_text || entry.user_comment) {
            var contentSec = el('div', 'drawer-section');
            contentSec.appendChild(el('h4', null, 'Content'));

            if (entry.correction_text) {
                var cLbl = el('div', 'content-panel-label', 'User Correction');
                var cPanel = el('pre', 'content-panel correction'); cPanel.textContent = entry.correction_text;
                contentSec.appendChild(cLbl); contentSec.appendChild(cPanel);
            }

            if (entry.user_comment) {
                var ucLbl = el('div', 'content-panel-label mt-2', 'User Comment');
                var ucVal = el('p', 'text-small'); ucVal.textContent = entry.user_comment;
                contentSec.appendChild(ucLbl); contentSec.appendChild(ucVal);
            }
            frag.appendChild(contentSec);
        }

        // If already reviewed, show who reviewed and when (tags/comment are pre-filled in the form below)
        if (entry.review_status !== 'pending_review' && entry.reviewer_id) {
            var prevSec = el('div', 'drawer-section');
            prevSec.appendChild(el('h4', null, 'Previous Decision'));
            var prevRow = el('div', 'meta-row');
            prevRow.appendChild(metaItem('By', entry.reviewer_id));
            prevRow.appendChild(metaItem('At', fmtDate(entry.reviewed_at)));
            prevSec.appendChild(prevRow);
            frag.appendChild(prevSec);
        }

        // Synthesis section — only for approved entries
        if (entry.review_status === 'approved') {
            frag.appendChild(buildSynthesisSection(entry));
        }

        // Rollback info for non-approved entries (approved entries show it inside the synthesis section)
        if (entry.rolled_back_at && entry.review_status !== 'approved') {
            var rbSec = el('div', 'drawer-section');
            rbSec.appendChild(el('h4', null, 'Rollback History'));
            var rbRow = el('div', 'meta-row');
            rbRow.appendChild(metaItem('Rolled Back At', fmtDate(entry.rolled_back_at)));
            if (entry.rolled_back_by) rbRow.appendChild(metaItem('By', entry.rolled_back_by));
            rbSec.appendChild(rbRow);
            if (entry.rollback_reason) {
                var rbReasonRow = el('div', 'meta-row');
                rbReasonRow.appendChild(metaItem('Reason', entry.rollback_reason));
                rbSec.appendChild(rbReasonRow);
            }
            frag.appendChild(rbSec);
        }

        // Review form
        var form = buildReviewForm(entry);
        frag.appendChild(form);

        return frag;
    }

    function buildSynthesisSection(entry) {
        var sec = el('div', 'drawer-section');
        sec.appendChild(el('h4', null, 'KB Synthesis'));

        if (entry.integrated_into_kb_id) {
            // Currently synthesized — show status + rollback option
            var row = el('div', 'meta-row');
            row.appendChild(metaItem('Status', 'Synthesized ✓'));
            if (entry.integrated_at) row.appendChild(metaItem('At', fmtDate(entry.integrated_at)));
            row.appendChild(metaItem('KB Doc', trunc(String(entry.integrated_into_kb_id), 36)));
            sec.appendChild(row);

            var rbErr = el('div', 'inline-error'); rbErr.id = 'rb-err';
            sec.appendChild(rbErr);

            var rbBtn = el('button', 'btn-warning', 'Roll Back');
            rbBtn.title = 'Remove this synthesized KB article and revert feedback entries for re-synthesis';
            rbBtn.addEventListener('click', function () {
                var promptResult = window.prompt('Reason for rollback (optional):');
                if (promptResult === null) return; // Cancel aborts
                var reason = promptResult.trim() || null;
                if (reason === null && !window.confirm('Roll back this article without a reason?')) return;
                rbBtn.disabled = true;
                rbBtn.textContent = 'Rolling back…';
                rbErr.classList.remove('visible');

                apiPost('/synthesis/rollback/' + encodeURIComponent(entry.integrated_into_kb_id),
                    reason !== null ? { reason: reason } : {})
                    .then(function (data) {
                        var count = (data && data.feedback_entries_reverted) || 0;
                        showToast('Article rolled back. ' + count + ' feedback entr' + (count === 1 ? 'y' : 'ies') + ' reverted.', 'success');
                        hide('reviewDrawer');
                        if (currentView === 'feedback-reviewed') loadFeedbackReviewed(_frState);
                    })
                    .catch(function (e) {
                        if (e.status === 422) {
                            rbErr.textContent = (e.data && e.data.detail) || 'This document is not a synthesized article.';
                        } else if (e.status === 500) {
                            rbErr.textContent = (e.data && e.data.detail) || 'Rollback failed — check server logs.';
                        } else {
                            rbErr.textContent = 'Rollback failed: ' + String(e);
                        }
                        rbErr.classList.add('visible');
                        rbBtn.disabled = false;
                        rbBtn.textContent = 'Roll Back';
                    });
            });
            sec.appendChild(rbBtn);
        } else if (entry.rolled_back_at) {
            // Previously synthesized but rolled back — re-synthesis is available
            var rbInfoRow = el('div', 'meta-row');
            rbInfoRow.appendChild(metaItem('Status', 'Rolled Back ↩'));
            rbInfoRow.appendChild(metaItem('On', fmtDate(entry.rolled_back_at)));
            if (entry.rolled_back_by) rbInfoRow.appendChild(metaItem('By', entry.rolled_back_by));
            sec.appendChild(rbInfoRow);
            if (entry.rollback_reason) {
                var rbReasonRow = el('div', 'meta-row');
                rbReasonRow.appendChild(metaItem('Reason', entry.rollback_reason));
                sec.appendChild(rbReasonRow);
            }
            var rehint = el('p', 'text-muted text-small',
                'This entry was rolled back and is eligible for re-synthesis. ' +
                'Click below to synthesize it again.');
            sec.appendChild(rehint);

            var synthErr = el('div', 'inline-error'); synthErr.id = 'synth-err';
            sec.appendChild(synthErr);

            var resynthBtn = el('button', 'btn-success', 'Re-synthesize into KB');
            resynthBtn.addEventListener('click', function () {
                resynthBtn.disabled = true;
                resynthBtn.textContent = 'Synthesizing…';
                synthErr.classList.remove('visible');

                apiPost('/synthesis/trigger/' + encodeURIComponent(entry.id), {})
                    .then(function () {
                        showToast('Entry re-synthesized into KB.', 'success');
                        hide('reviewDrawer');
                        if (currentView === 'feedback-reviewed') loadFeedbackReviewed(_frState);
                    })
                    .catch(function (e) {
                        if (e.status === 409) {
                            synthErr.textContent = 'Already synthesized — reload to refresh.';
                        } else if (e.status === 422) {
                            synthErr.textContent = (e.data && e.data.detail) || 'Validation error.';
                        } else {
                            synthErr.textContent = 'Synthesis failed: ' + String(e);
                        }
                        synthErr.classList.add('visible');
                        resynthBtn.disabled = false;
                        resynthBtn.textContent = 'Re-synthesize into KB';
                    });
            });
            sec.appendChild(resynthBtn);
        } else {
            var hint = el('p', 'text-muted text-small',
                'This approved entry has not yet been synthesized into the knowledge base. ' +
                'Click below to synthesize it immediately.');
            sec.appendChild(hint);

            var synthErr = el('div', 'inline-error'); synthErr.id = 'synth-err';
            sec.appendChild(synthErr);

            var synthBtn = el('button', 'btn-success', 'Synthesize into KB');
            synthBtn.addEventListener('click', function () {
                synthBtn.disabled = true;
                synthBtn.textContent = 'Synthesizing…';
                synthErr.classList.remove('visible');

                apiPost('/synthesis/trigger/' + encodeURIComponent(entry.id), {})
                    .then(function () {
                        showToast('Entry synthesized into KB.', 'success');
                        hide('reviewDrawer');
                        if (currentView === 'feedback-reviewed') loadFeedbackReviewed(_frState);
                    })
                    .catch(function (e) {
                        if (e.status === 409) {
                            synthErr.textContent = 'Already synthesized — reload to refresh the entry.';
                        } else if (e.status === 422) {
                            synthErr.textContent = (e.data && e.data.detail) || 'Validation error: entry is not eligible for synthesis.';
                        } else {
                            synthErr.textContent = 'Synthesis failed: ' + String(e);
                        }
                        synthErr.classList.add('visible');
                        synthBtn.disabled = false;
                        synthBtn.textContent = 'Synthesize into KB';
                    });
            });
            sec.appendChild(synthBtn);
        }

        return sec;
    }

    function buildReviewForm(entry) {
        var formSec = el('div', 'review-form');
        formSec.appendChild(el('h4', null, 'Decision'));

        // Status radio
        var statusRow = el('div', 'form-row');
        var statusLbl = el('label', 'form-label', 'Decision *');
        var radioGrp = el('div', 'radio-group');
        ['approved', 'rejected'].forEach(function (val) {
            var lbl = el('label', 'radio-label');
            var inp = document.createElement('input');
            inp.type = 'radio'; inp.name = 'review_status'; inp.value = val;
            inp.id = 'r-' + val;
            if (entry.review_status === val) inp.checked = true;
            lbl.appendChild(inp);
            lbl.appendChild(document.createTextNode(' ' + (val === 'approved' ? '✅ Approved' : '❌ Rejected')));
            radioGrp.appendChild(lbl);
        });
        statusRow.appendChild(statusLbl); statusRow.appendChild(radioGrp);
        formSec.appendChild(statusRow);

        var statusErr = el('div', 'inline-error'); statusErr.id = 'review-status-err';
        formSec.appendChild(statusErr);

        // Tags chip input
        var tagsRow = el('div', 'form-row');
        tagsRow.appendChild(el('label', 'form-label', 'Tags'));
        var chipWrap = el('div', 'chip-input-wrap'); chipWrap.id = 'review-tags-wrap';
        var chipInput = el('input', 'chip-text-input');
        chipInput.type = 'text'; chipInput.placeholder = 'Type and press Enter or ,';
        chipWrap.appendChild(chipInput);
        tagsRow.appendChild(chipWrap);
        tagsRow.appendChild(el('div', 'form-hint', 'Max 20 tags, ≤32 chars each, [A-Za-z0-9_-]'));
        formSec.appendChild(tagsRow);

        var tagsErr = el('div', 'inline-error'); tagsErr.id = 'review-tags-err';
        formSec.appendChild(tagsErr);

        // Prefill existing tags
        var currentTags = (entry.reviewer_tags || []).slice();
        currentTags.forEach(function (t) { addChipTag(chipWrap, chipInput, currentTags, t); });

        wireChipInput(chipWrap, chipInput, currentTags, 'review-tags-err');

        // Comment textarea
        var commentRow = el('div', 'form-row');
        commentRow.appendChild(el('label', 'form-label', 'Comment'));
        var commentTa = el('textarea', 'form-textarea'); commentTa.id = 'review-comment'; commentTa.rows = 3;
        commentTa.placeholder = 'Optional reviewer comment…';
        if (entry.reviewer_comment) commentTa.value = entry.reviewer_comment;
        commentRow.appendChild(commentTa);
        formSec.appendChild(commentRow);

        // Transition note (409 handling)
        var transErr = el('div', 'inline-error'); transErr.id = 'review-transition-err';
        formSec.appendChild(transErr);

        // Footer
        var footer = el('div', 'drawer-footer');
        var saveBtn = el('button', 'btn-primary', 'Save Decision');
        saveBtn.id = 'review-save-btn';
        var cancelBtn = el('button', 'btn-secondary', 'Cancel');
        cancelBtn.addEventListener('click', function () { hide('reviewDrawer'); });
        footer.appendChild(cancelBtn);
        footer.appendChild(saveBtn);
        formSec.appendChild(footer);

        saveBtn.addEventListener('click', function () {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';
            var statusEl = qs('input[name="review_status"]:checked', formSec);
            var statusVal = statusEl ? statusEl.value : '';

            // Validate status
            var statusErrEl = document.getElementById('review-status-err');
            if (!statusVal) {
                statusErrEl.textContent = 'Please select a decision.';
                statusErrEl.classList.add('visible');
                saveBtn.disabled = false; saveBtn.textContent = 'Save Decision';
                return;
            }
            statusErrEl.classList.remove('visible');

            var body = { review_status: statusVal };
            if (currentTags.length > 0) body.reviewer_tags = currentTags.slice();
            var commentVal = document.getElementById('review-comment').value.trim();
            if (commentVal) body.reviewer_comment = commentVal;

            apiPatch('/feedback/' + encodeURIComponent(entry.id), body)
                .then(function (updated) {
                    showToast('Decision saved.', 'success');
                    hide('reviewDrawer');
                    // Refresh whichever list view is currently active
                    loadFeedbackQueue(_fqState);
                    if (currentView === 'feedback-reviewed') loadFeedbackReviewed(_frState);
                    updatePendingBadge();
                })
                .catch(function (e) {
                    var transErrEl = document.getElementById('review-transition-err');
                    if (e.status === 409) {
                        transErrEl.textContent = (e.data && e.data.detail) ||
                            'Status transition not allowed. Reload the entry and try again.';
                        transErrEl.classList.add('visible');
                    } else if (e.status === 422) {
                        transErrEl.textContent = 'Validation error: ' + (e.data && e.data.detail ? e.data.detail : String(e));
                        transErrEl.classList.add('visible');
                    } else {
                        showToast('Error saving: ' + String(e), 'error');
                    }
                    saveBtn.disabled = false; saveBtn.textContent = 'Save Decision';
                });
        });

        return formSec;
    }

    // ================================================================
    // KB DOCUMENT EDITOR
    // ================================================================

    function openKBEditor(rawId) {
        var body = document.getElementById('kbEditorBody');
        body.innerHTML = '';
        body.appendChild(buildLoadingRow());
        show('kbEditor');

        apiGet('/kb/documents/' + encodeURIComponent(rawId))
            .then(function (doc) {
                body.innerHTML = '';
                body.appendChild(buildKBEditorContent(doc));
            })
            .catch(function (e) {
                body.innerHTML = '';
                body.appendChild(el('p', 'text-muted', 'Failed to load document: ' + String(e)));
            });
    }

    document.getElementById('kbEditorClose').addEventListener('click', function () { hide('kbEditor'); });
    document.getElementById('kbEditor').addEventListener('click', function (ev) {
        if (ev.target === this) hide('kbEditor');
    });

    function buildKBEditorContent(doc) {
        var frag = document.createDocumentFragment();

        // Read-only header
        var metaSec = el('div', 'drawer-section');
        metaSec.appendChild(el('h4', null, 'Document Info'));
        var metaRow = el('div', 'meta-row');
        metaRow.appendChild(metaItem('ID', trunc(doc.id, 48)));
        metaRow.appendChild(metaItem('Source', doc.source || '—'));
        if (doc.source_url) metaRow.appendChild(metaItem('URL', trunc(doc.source_url, 40)));
        metaRow.appendChild(metaItem('Created', fmtDate(doc.created_at)));
        metaRow.appendChild(metaItem('Chunks', doc.chunk_count != null ? String(doc.chunk_count) : '—'));
        metaSec.appendChild(metaRow);
        frag.appendChild(metaSec);

        // Re-embed warning (shown when content is dirty)
        var warnBanner = el('div', 'warning-banner');
        warnBanner.id = 'kb-reembed-warn';
        warnBanner.textContent = '⚠ Saving changes to content will re-embed this document and may take several seconds.';
        frag.appendChild(warnBanner);

        // Editable fields
        var editSec = el('div', 'drawer-section');
        editSec.appendChild(el('h4', null, 'Edit Fields'));

        var originalContent = doc.content || '';

        // Title
        var titleRow = el('div', 'form-row');
        titleRow.appendChild(el('label', 'form-label', 'Title'));
        var titleInp = el('input', 'form-input'); titleInp.type = 'text'; titleInp.id = 'kb-title';
        titleInp.value = doc.title || '';
        titleRow.appendChild(titleInp); editSec.appendChild(titleRow);

        // Topic
        var topicRow = el('div', 'form-row');
        topicRow.appendChild(el('label', 'form-label', 'Topic'));
        var topicInp = el('input', 'form-input'); topicInp.type = 'text'; topicInp.id = 'kb-topic';
        topicInp.value = doc.topic || '';
        topicRow.appendChild(topicInp); editSec.appendChild(topicRow);

        // Tags chip input
        var kbTagsRow = el('div', 'form-row');
        kbTagsRow.appendChild(el('label', 'form-label', 'Tags'));
        var kbChipWrap = el('div', 'chip-input-wrap'); kbChipWrap.id = 'kb-tags-wrap';
        var kbChipInput = el('input', 'chip-text-input');
        kbChipInput.type = 'text'; kbChipInput.placeholder = 'Type and press Enter or ,';
        kbChipWrap.appendChild(kbChipInput);
        kbTagsRow.appendChild(kbChipWrap);
        kbTagsRow.appendChild(el('div', 'form-hint', 'Max 20 tags, ≤32 chars each, [A-Za-z0-9_-]'));
        editSec.appendChild(kbTagsRow);

        var kbTagsErr = el('div', 'inline-error'); kbTagsErr.id = 'kb-tags-err';
        editSec.appendChild(kbTagsErr);

        var currentKBTags = (doc.tags || []).slice();
        currentKBTags.forEach(function (t) { addChipTag(kbChipWrap, kbChipInput, currentKBTags, t); });
        wireChipInput(kbChipWrap, kbChipInput, currentKBTags, 'kb-tags-err');

        // Date published
        var dateRow = el('div', 'form-row');
        dateRow.appendChild(el('label', 'form-label', 'Date Published'));
        var dateInp = el('input', 'form-input'); dateInp.type = 'date'; dateInp.id = 'kb-date-published';
        if (doc.date_published) {
            try { dateInp.value = doc.date_published.slice(0, 10); } catch (e) { /* ignore */ }
        }
        dateRow.appendChild(dateInp); editSec.appendChild(dateRow);

        // Content (monospace textarea)
        var contentRow = el('div', 'form-row');
        contentRow.appendChild(el('label', 'form-label', 'Content'));
        var contentTa = el('textarea', 'form-textarea mono'); contentTa.id = 'kb-content';
        contentTa.value = doc.content || '';
        contentTa.addEventListener('input', function () {
            var warn = document.getElementById('kb-reembed-warn');
            if (warn) warn.classList.toggle('visible', contentTa.value !== originalContent);
        });
        contentRow.appendChild(contentTa); editSec.appendChild(contentRow);

        // Metadata (JSON textarea)
        var metaRow2 = el('div', 'form-row');
        metaRow2.appendChild(el('label', 'form-label', 'Metadata (JSON)'));
        var metaTa = el('textarea', 'form-textarea mono'); metaTa.id = 'kb-metadata'; metaTa.rows = 5;
        try { metaTa.value = JSON.stringify(doc.metadata || {}, null, 2); } catch (e) { metaTa.value = '{}'; }
        metaRow2.appendChild(metaTa);
        var metaErr = el('div', 'inline-error'); metaErr.id = 'kb-metadata-err';
        metaRow2.appendChild(metaErr);
        editSec.appendChild(metaRow2);

        frag.appendChild(editSec);

        // Save / Delete / Rollback footer
        var footer = el('div', 'drawer-footer');
        var deleteBtn = el('button', 'btn-danger', 'Delete Document');
        var saveBtn = el('button', 'btn-primary', 'Save Changes');
        var cancelBtn = el('button', 'btn-secondary', 'Cancel');
        cancelBtn.addEventListener('click', function () { hide('kbEditor'); });

        var footerRight = el('div', 'drawer-footer-right');
        footerRight.appendChild(cancelBtn); footerRight.appendChild(saveBtn);
        footer.appendChild(deleteBtn);

        // Roll Back button — only for synthesized articles
        if (doc.source === 'feedback') {
            var rbFooterErr = el('div', 'inline-error'); rbFooterErr.id = 'kb-rb-err';
            frag.appendChild(rbFooterErr);

            var rollbackBtn = el('button', 'btn-warning', 'Roll Back Article');
            rollbackBtn.title = 'Remove this synthesized KB article and revert its source feedback entries for re-synthesis';
            rollbackBtn.addEventListener('click', function () {
                var promptResult = window.prompt('Reason for rollback (optional):');
                if (promptResult === null) return; // Cancel aborts
                var reason = promptResult.trim() || null;
                if (reason === null && !window.confirm('Roll back this article without a reason?')) return;
                rollbackBtn.disabled = true;
                rollbackBtn.textContent = 'Rolling back…';
                rbFooterErr.classList.remove('visible');

                apiPost('/synthesis/rollback/' + encodeURIComponent(doc.id),
                    reason !== null ? { reason: reason } : {})
                    .then(function (data) {
                        var count = (data && data.feedback_entries_reverted) || 0;
                        showToast('Article rolled back. ' + count + ' feedback entr' + (count === 1 ? 'y' : 'ies') + ' reverted.', 'success');
                        hide('kbEditor');
                        loadKBBrowser(_kbState);
                    })
                    .catch(function (e) {
                        if (e.status === 422) {
                            rbFooterErr.textContent = (e.data && e.data.detail) || 'This document is not a synthesized article.';
                        } else if (e.status === 500) {
                            rbFooterErr.textContent = (e.data && e.data.detail) || 'Rollback failed — check server logs.';
                        } else {
                            rbFooterErr.textContent = 'Rollback failed: ' + String(e);
                        }
                        rbFooterErr.classList.add('visible');
                        rollbackBtn.disabled = false;
                        rollbackBtn.textContent = 'Roll Back Article';
                    });
            });
            footer.appendChild(rollbackBtn);
        }

        footer.appendChild(footerRight);
        frag.appendChild(footer);

        // Save handler
        saveBtn.addEventListener('click', function () {
            saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
            var metaErrEl = document.getElementById('kb-metadata-err');
            metaErrEl.classList.remove('visible');

            var body = {};
            var titleVal = document.getElementById('kb-title').value.trim();
            if (titleVal !== (doc.title || '')) body.title = titleVal || null;
            var topicVal = document.getElementById('kb-topic').value.trim();
            if (topicVal !== (doc.topic || '')) body.topic = topicVal || null;
            if (JSON.stringify(currentKBTags) !== JSON.stringify(doc.tags || [])) body.tags = currentKBTags.slice();
            var dateVal = document.getElementById('kb-date-published').value;
            if (dateVal !== (doc.date_published || '').slice(0, 10)) body.date_published = dateVal || null;
            var contentVal = document.getElementById('kb-content').value;
            if (contentVal !== doc.content) body.content = contentVal;

            // Parse metadata JSON
            var metaVal = document.getElementById('kb-metadata').value.trim();
            if (metaVal) {
                try {
                    var parsed = JSON.parse(metaVal);
                    if (JSON.stringify(parsed) !== JSON.stringify(doc.metadata || {})) body.metadata = parsed;
                } catch (e) {
                    metaErrEl.textContent = 'Invalid JSON: ' + e.message;
                    metaErrEl.classList.add('visible');
                    saveBtn.disabled = false; saveBtn.textContent = 'Save Changes';
                    return;
                }
            }

            if (Object.keys(body).length === 0) {
                showToast('No changes to save.', 'info');
                saveBtn.disabled = false; saveBtn.textContent = 'Save Changes';
                return;
            }

            apiPatch('/kb/documents/' + encodeURIComponent(doc.id), body)
                .then(function (updated) {
                    showToast('Document saved.', 'success');
                    hide('kbEditor');
                    loadKBBrowser(_kbState);
                })
                .catch(function (e) {
                    showToast('Error saving: ' + String(e), 'error');
                    saveBtn.disabled = false; saveBtn.textContent = 'Save Changes';
                });
        });

        // Delete handler
        deleteBtn.addEventListener('click', function () {
            var docLabel = trunc(doc.title || doc.id, 40);
            showConfirm(
                'Delete Document?',
                'This will permanently delete "' + docLabel + '" and all its chunks. This cannot be undone.',
                'Delete'
            ).then(function (confirmed) {
                if (!confirmed) return;
                apiDelete('/kb/documents/' + encodeURIComponent(doc.id))
                    .then(function () {
                        showToast('Document deleted.', 'success');
                        hide('kbEditor');
                        loadKBBrowser(_kbState);
                    })
                    .catch(function (e) { showToast('Delete failed: ' + String(e), 'error'); });
            });
        });

        return frag;
    }

    // ================================================================
    // CHIP INPUT WIDGET
    // ================================================================

    var TAG_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;
    var MAX_TAGS = 20;

    function validateTag(tag) {
        if (!tag) return 'Tag cannot be empty.';
        if (tag.length > 32) return 'Tag must be ≤32 characters.';
        if (!TAG_PATTERN.test(tag)) return 'Tag must match [A-Za-z0-9_-].';
        return null;
    }

    function addChipTag(wrap, textInput, tagsArray, tag) {
        var chip = el('span', 'chip-tag');
        chip.appendChild(document.createTextNode(tag));
        var rm = el('button', 'chip-remove', '×');
        rm.type = 'button';
        rm.setAttribute('aria-label', 'Remove tag ' + tag);
        rm.addEventListener('click', function () {
            var idx = tagsArray.indexOf(tag);
            if (idx >= 0) tagsArray.splice(idx, 1);
            wrap.removeChild(chip);
        });
        chip.appendChild(rm);
        // Insert before the text input
        wrap.insertBefore(chip, textInput);
    }

    function wireChipInput(wrap, textInput, tagsArray, errElId) {
        function tryAdd(raw) {
            var tag = raw.trim();
            var errEl = document.getElementById(errElId);
            if (!tag) return;
            var err = validateTag(tag);
            if (err) {
                if (errEl) { errEl.textContent = err; errEl.classList.add('visible'); }
                return;
            }
            if (tagsArray.indexOf(tag) >= 0) {
                if (errEl) { errEl.textContent = 'Tag already added.'; errEl.classList.add('visible'); }
                return;
            }
            if (tagsArray.length >= MAX_TAGS) {
                if (errEl) { errEl.textContent = 'Maximum ' + MAX_TAGS + ' tags allowed.'; errEl.classList.add('visible'); }
                return;
            }
            if (errEl) errEl.classList.remove('visible');
            tagsArray.push(tag);
            addChipTag(wrap, textInput, tagsArray, tag);
            textInput.value = '';
        }

        textInput.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter' || ev.key === ',') {
                ev.preventDefault();
                tryAdd(textInput.value);
            } else if (ev.key === 'Backspace' && textInput.value === '' && tagsArray.length > 0) {
                var last = tagsArray[tagsArray.length - 1];
                tagsArray.splice(tagsArray.length - 1, 1);
                var chips = qsa('.chip-tag', wrap);
                var lastChip = chips[chips.length - 1];
                if (lastChip) wrap.removeChild(lastChip);
            }
        });

        textInput.addEventListener('blur', function () {
            if (textInput.value.trim()) tryAdd(textInput.value);
        });

        wrap.addEventListener('click', function () { textInput.focus(); });
    }

    // ================================================================
    // SHARED UI HELPERS
    // ================================================================

    function makeChip(label, chipClass) {
        var chip = el('span', 'chip ' + (chipClass || ''));
        chip.textContent = label;
        return chip;
    }

    function makeTagList(tags) {
        var wrap = el('div', 'tag-list');
        (tags || []).slice(0, 5).forEach(function (t) {
            var tag = el('span', 'tag'); tag.textContent = t; wrap.appendChild(tag);
        });
        if (tags && tags.length > 5) {
            wrap.appendChild(el('span', 'tag text-muted', '+' + (tags.length - 5)));
        }
        return wrap;
    }

    function metaItem(key, value) {
        var wrap = el('div', 'meta-item');
        wrap.appendChild(el('span', 'mkey', key));
        var val = el('span', 'mval'); val.textContent = value || '—'; wrap.appendChild(val);
        return wrap;
    }

    function buildSelect(id, labelText, options) {
        var grp = el('div', 'filter-group');
        var lbl = el('label', null, labelText); lbl.htmlFor = id; grp.appendChild(lbl);
        var sel = el('select'); sel.id = id;
        options.forEach(function (opt) {
            var o = document.createElement('option');
            o.value = opt[0]; o.textContent = opt[1]; sel.appendChild(o);
        });
        grp.appendChild(sel);
        return grp;
    }

    function buildTextInput(id, labelText, placeholder) {
        var grp = el('div', 'filter-group');
        var lbl = el('label', null, labelText); lbl.htmlFor = id; grp.appendChild(lbl);
        var inp = el('input', 'form-input'); inp.type = 'text'; inp.id = id;
        if (placeholder) inp.placeholder = placeholder;
        grp.appendChild(inp);
        return grp;
    }

    function buildDateInput(id, labelText) {
        var grp = el('div', 'filter-group');
        var lbl = el('label', null, labelText); lbl.htmlFor = id; grp.appendChild(lbl);
        var inp = el('input', 'form-input'); inp.type = 'date'; inp.id = id;
        grp.appendChild(inp);
        return grp;
    }

    function renderPagination(container, total, offset, limit, onNavigate) {
        container.innerHTML = '';
        if (!total) return;
        var currentPage = Math.floor(offset / limit);
        var totalPages  = Math.ceil(total / limit);

        var info = el('span', 'pagination-info');
        info.textContent = 'Showing ' + (offset + 1) + '–' + Math.min(offset + limit, total) + ' of ' + total;
        container.appendChild(info);

        var prevBtn = el('button', 'btn-secondary', '← Prev');
        prevBtn.style.padding = '6px 12px';
        prevBtn.disabled = offset === 0;
        prevBtn.addEventListener('click', function () { onNavigate(offset - limit); });
        container.appendChild(prevBtn);

        var pg = el('span'); pg.textContent = 'Page ' + (currentPage + 1) + ' of ' + totalPages;
        container.appendChild(pg);

        var nextBtn = el('button', 'btn-secondary', 'Next →');
        nextBtn.style.padding = '6px 12px';
        nextBtn.disabled = offset + limit >= total;
        nextBtn.addEventListener('click', function () { onNavigate(offset + limit); });
        container.appendChild(nextBtn);
    }

    function buildLoadingRow() {
        var wrap = el('div');
        wrap.style.cssText = 'padding:40px;text-align:center;color:#a0aec0';
        var spinner = el('div', 'loading-spinner'); spinner.style.margin = '0 auto 12px';
        wrap.appendChild(spinner);
        wrap.appendChild(document.createTextNode('Loading…'));
        return wrap;
    }

    function buildErrorRow(msg) {
        var p = el('p'); p.style.cssText = 'padding:24px;color:#e53e3e;';
        p.textContent = 'Error: ' + msg;
        return p;
    }

    // ================================================================
    // INITIALISATION
    // ================================================================

    function init() {
        // Probe capabilities
        fetch(P + '/_capabilities', { credentials: 'include' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (caps) {
                hide('loadingState');

                if (!caps || !caps.is_admin) {
                    show('accessDenied');
                    return;
                }

                if (caps.anonymous) {
                    show('devBanner');
                    // Push content down below the fixed banner
                    var app = document.getElementById('dashboardApp');
                    if (app) app.style.paddingTop = '40px';
                }

                show('dashboardApp');
                initNavigation();

                // Render shells for the table views
                renderFeedbackQueueShell();
                renderFeedbackReviewedShell();
                renderKBBrowserShell();

                // Load initial data
                loadFeedbackQueue(_fqState);
                updatePendingBadge();
            })
            .catch(function () {
                hide('loadingState');
                show('accessDenied');
            });
    }

    document.addEventListener('DOMContentLoaded', init);
})();
