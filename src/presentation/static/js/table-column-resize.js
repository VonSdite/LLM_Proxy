(function () {
    "use strict";

    const tableSelector = "table[data-resizable-columns]";
    const handleClass = "table-column-resize-handle";
    const defaultMinWidth = 48;
    const defaultMaxWidth = Number.POSITIVE_INFINITY;
    const resizeStep = 16;
    const groupStates = new Map();
    let anonymousGroupSequence = 0;
    let activeResize = null;

    function parseWidth(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function clampResizeDelta(delta, currentWidth, nextWidth, currentLimits, nextLimits) {
        const minDelta = Math.max(currentLimits.minWidth - currentWidth, nextWidth - nextLimits.maxWidth);
        const maxDelta = Math.min(currentLimits.maxWidth - currentWidth, nextWidth - nextLimits.minWidth);
        return Math.min(maxDelta, Math.max(minDelta, Math.round(delta)));
    }

    function getGroupKey(table) {
        const configuredKey = String(table.dataset.resizableColumns || "").trim();
        if (configuredKey) return configuredKey;
        if (!table.dataset.resizableColumnsRuntimeKey) {
            anonymousGroupSequence += 1;
            table.dataset.resizableColumnsRuntimeKey = `table-${anonymousGroupSequence}`;
        }
        return table.dataset.resizableColumnsRuntimeKey;
    }

    function getHeaderCells(table) {
        const row = table.tHead?.rows?.[0];
        if (!row) return [];
        return Array.from(row.cells).filter(cell => Number(cell.colSpan || 1) === 1);
    }

    function getColumnElements(table, columnCount) {
        let colgroup = Array.from(table.children).find(child => child.tagName === "COLGROUP") || null;
        if (!colgroup) {
            colgroup = document.createElement("colgroup");
            table.insertBefore(colgroup, table.firstChild);
        }
        while (colgroup.children.length < columnCount) {
            colgroup.appendChild(document.createElement("col"));
        }
        return Array.from(colgroup.children).slice(0, columnCount);
    }

    function captureGroupState(table) {
        const widths = getHeaderCells(table).map(cell => cell.getBoundingClientRect().width);
        if (!widths.length || widths.some(width => width <= 0)) return null;
        const state = { widths, tableWidth: table.getBoundingClientRect().width };
        groupStates.set(getGroupKey(table), state);
        return state;
    }

    function applyStateToTable(table, state) {
        const headers = getHeaderCells(table);
        if (!headers.length || headers.length !== state.widths.length) return;
        const columns = getColumnElements(table, headers.length);
        state.widths.forEach((width, index) => {
            columns[index].style.width = `${width}px`;
        });
        table.style.tableLayout = "fixed";
        table.style.width = `min(100%, ${state.tableWidth}px)`;
        table.style.minWidth = "0";
        headers.forEach((header, index) => {
            header
                .querySelector(`.${handleClass}`)
                ?.setAttribute("aria-valuenow", String(Math.round(state.widths[index])));
        });
    }

    function applyGroupState(groupKey, state) {
        document.querySelectorAll(tableSelector).forEach(table => {
            if (getGroupKey(table) === groupKey) {
                applyStateToTable(table, state);
            }
        });
    }

    function getColumnLimits(header) {
        return {
            minWidth: parseWidth(header?.dataset.resizeMinWidth, defaultMinWidth),
            maxWidth: parseWidth(header?.dataset.resizeMaxWidth, defaultMaxWidth),
        };
    }

    function updateColumnBoundary(
        groupKey,
        state,
        columnIndex,
        currentWidth,
        nextWidth,
        delta,
        currentLimits,
        nextLimits
    ) {
        const resizeDelta = clampResizeDelta(delta, currentWidth, nextWidth, currentLimits, nextLimits);
        state.widths[columnIndex] = currentWidth + resizeDelta;
        state.widths[columnIndex + 1] = nextWidth - resizeDelta;
        applyGroupState(groupKey, state);
    }

    function finishResize(event) {
        if (!activeResize) return;
        if (event?.pointerId !== undefined && event.pointerId !== activeResize.pointerId) return;
        const { handle, pointerId } = activeResize;
        if (handle.hasPointerCapture?.(pointerId)) {
            handle.releasePointerCapture(pointerId);
        }
        handle.classList.remove("is-resizing");
        document.body.classList.remove("is-resizing-table-column");
        activeResize = null;
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", finishResize);
        window.removeEventListener("pointercancel", finishResize);
    }

    function handlePointerMove(event) {
        if (!activeResize || event.pointerId !== activeResize.pointerId) return;
        updateColumnBoundary(
            activeResize.groupKey,
            activeResize.state,
            activeResize.columnIndex,
            activeResize.startWidth,
            activeResize.nextStartWidth,
            event.clientX - activeResize.startX,
            activeResize.currentLimits,
            activeResize.nextLimits
        );
    }

    function startResize(event) {
        if (event.button !== 0) return;
        const handle = event.currentTarget;
        const table = handle.closest(tableSelector);
        if (!table) return;
        const groupKey = getGroupKey(table);
        const state = groupStates.get(groupKey) || captureGroupState(table);
        if (!state) return;
        const columnIndex = Number(handle.dataset.columnIndex);
        if (!Number.isInteger(columnIndex) || columnIndex < 0 || columnIndex >= state.widths.length - 1) return;
        const headers = getHeaderCells(table);
        event.preventDefault();
        event.stopPropagation();
        finishResize();
        activeResize = {
            groupKey,
            state,
            columnIndex,
            pointerId: event.pointerId,
            startX: event.clientX,
            startWidth: state.widths[columnIndex],
            nextStartWidth: state.widths[columnIndex + 1],
            currentLimits: getColumnLimits(headers[columnIndex]),
            nextLimits: getColumnLimits(headers[columnIndex + 1]),
            handle,
        };
        handle.setPointerCapture?.(event.pointerId);
        handle.classList.add("is-resizing");
        document.body.classList.add("is-resizing-table-column");
        window.addEventListener("pointermove", handlePointerMove);
        window.addEventListener("pointerup", finishResize);
        window.addEventListener("pointercancel", finishResize);
    }

    function handleResizeKeydown(event) {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        const handle = event.currentTarget;
        const table = handle.closest(tableSelector);
        if (!table) return;
        const groupKey = getGroupKey(table);
        const state = groupStates.get(groupKey) || captureGroupState(table);
        if (!state) return;
        const columnIndex = Number(handle.dataset.columnIndex);
        if (!Number.isInteger(columnIndex) || columnIndex < 0 || columnIndex >= state.widths.length - 1) return;
        event.preventDefault();
        event.stopPropagation();
        const direction = event.key === "ArrowLeft" ? -1 : 1;
        const headers = getHeaderCells(table);
        updateColumnBoundary(
            groupKey,
            state,
            columnIndex,
            state.widths[columnIndex],
            state.widths[columnIndex + 1],
            direction * resizeStep,
            getColumnLimits(headers[columnIndex]),
            getColumnLimits(headers[columnIndex + 1])
        );
    }

    function addResizeHandles(table) {
        if (table.dataset.resizableColumnsReady === "true") {
            const state = groupStates.get(getGroupKey(table));
            if (state) applyStateToTable(table, state);
            return;
        }
        const headers = getHeaderCells(table);
        if (!headers.length) return;
        headers.slice(0, -1).forEach((header, columnIndex) => {
            header.classList.add("resizable-column-header");
            const handle = document.createElement("span");
            const label = String(header.textContent || "").trim() || `第 ${columnIndex + 1} 列`;
            handle.className = handleClass;
            handle.dataset.columnIndex = String(columnIndex);
            handle.dataset.minWidth = header.dataset.resizeMinWidth || String(defaultMinWidth);
            handle.setAttribute("role", "separator");
            handle.setAttribute("tabindex", "0");
            handle.setAttribute("aria-label", `调整${label}列宽度`);
            handle.setAttribute("aria-orientation", "vertical");
            handle.setAttribute("aria-valuemin", handle.dataset.minWidth);
            if (header.dataset.resizeMaxWidth) {
                handle.setAttribute("aria-valuemax", header.dataset.resizeMaxWidth);
            }
            handle.setAttribute("title", `拖动调整${label}列宽度`);
            handle.addEventListener("pointerdown", startResize);
            handle.addEventListener("keydown", handleResizeKeydown);
            handle.addEventListener("click", event => event.stopPropagation());
            header.appendChild(handle);
        });
        table.dataset.resizableColumnsReady = "true";
        const state = groupStates.get(getGroupKey(table));
        if (state) applyStateToTable(table, state);
    }

    function initResizableTables(root = document) {
        if (root.matches?.(tableSelector)) addResizeHandles(root);
        root.querySelectorAll?.(tableSelector).forEach(addResizeHandles);
    }

    function startObserving() {
        initResizableTables();
        const observer = new MutationObserver(mutations => {
            if (mutations.some(mutation => mutation.type === "childList" || mutation.attributeName === "hidden")) {
                initResizableTables();
            }
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["hidden"],
        });
    }

    window.TableColumnResize = {
        init: initResizableTables,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startObserving, { once: true });
    } else {
        startObserving();
    }
})();
