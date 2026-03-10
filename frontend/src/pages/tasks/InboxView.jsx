import { useState, useCallback, useMemo } from "react";
import { DndContext, closestCenter, PointerSensor, TouchSensor, useSensor, useSensors, DragOverlay } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import InboxCard from "../../components/cards/InboxCard";
import { reorderTasks } from "../../lib/api";

function SortableTaskCard(props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: props.task.id });
  const isGroupDragged = props.isGroupDragged && !isDragging;
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging || isGroupDragged ? 0.3 : 1,
  };
  return (
    <div ref={setNodeRef} style={{ ...style, touchAction: "manipulation" }} {...attributes} {...listeners}>
      <InboxCard {...props} />
    </div>
  );
}

export default function InboxView({ tasks, loading, selecting, selected, onToggle, expandedTaskId, onExpandTask, onRefresh }) {
  const sorted = useMemo(() =>
    [...tasks].sort((a, b) => {
      if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
      return new Date(b.created_at) - new Date(a.created_at);
    }),
    [tasks]
  );

  const [activeDragId, setActiveDragId] = useState(null);

  // Is the dragged item part of a multi-selection?
  const isMultiDrag = activeDragId && selecting && selected.has(activeDragId) && selected.size > 1;
  const selectedIds = useMemo(() => {
    if (!selecting || selected.size <= 1) return null;
    // Preserve relative order from sorted list
    return sorted.filter(t => selected.has(t.id)).map(t => t.id);
  }, [selecting, selected, sorted]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 5 } }),
  );

  const handleDragStart = useCallback((event) => setActiveDragId(event.active.id), []);

  const handleDragEnd = useCallback(async (event) => {
    setActiveDragId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const ids = sorted.map(t => t.id);

    // Multi-drag: move selected group to the drop position
    if (selecting && selected.has(active.id) && selected.size > 1) {
      const groupIds = sorted.filter(t => selected.has(t.id)).map(t => t.id);
      const rest = ids.filter(id => !selected.has(id));
      // Find where "over" sits in the remaining list
      let insertIdx = rest.indexOf(over.id);
      if (insertIdx === -1) insertIdx = rest.length;
      // If dropping below the over item, insert after it
      const activeOrigIdx = ids.indexOf(active.id);
      const overOrigIdx = ids.indexOf(over.id);
      if (activeOrigIdx < overOrigIdx) insertIdx += 1;
      const newIds = [...rest.slice(0, insertIdx), ...groupIds, ...rest.slice(insertIdx)];
      await reorderTasks(newIds);
      onRefresh?.();
      return;
    }

    // Single drag
    const oldIdx = ids.indexOf(active.id);
    const newIdx = ids.indexOf(over.id);
    if (oldIdx === -1 || newIdx === -1) return;
    const newIds = arrayMove(ids, oldIdx, newIdx);
    await reorderTasks(newIds);
    onRefresh?.();
  }, [sorted, selecting, selected, onRefresh]);

  const handleDragCancel = useCallback(() => setActiveDragId(null), []);

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
        <p className="text-sm font-medium">Inbox zero</p>
        <p className="text-xs mt-1">Tap + to create a new task</p>
      </div>
    );
  }

  const activeTask = activeDragId ? sorted.find(t => t.id === activeDragId) : null;

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <SortableContext items={sorted.map(t => t.id)} strategy={verticalListSortingStrategy}>
        <div className="space-y-3" style={activeDragId ? { touchAction: "none" } : undefined}>
          {sorted.map((task) => (
            <SortableTaskCard
              key={task.id}
              task={task}
              selecting={selecting}
              selected={selected.has(task.id)}
              onToggle={onToggle}
              expanded={expandedTaskId === task.id}
              onExpand={onExpandTask}
              onRefresh={onRefresh}
              isGroupDragged={isMultiDrag && selected.has(task.id) && task.id !== activeDragId}
            />
          ))}
        </div>
      </SortableContext>
      <DragOverlay>
        {activeTask ? (
          <div className="relative opacity-90 scale-[1.02] shadow-xl rounded-xl">
            {/* Stacked cards effect for multi-drag */}
            {isMultiDrag && (
              <>
                <div className="absolute inset-0 bg-surface rounded-xl ring-1 ring-edge/20 -rotate-1 translate-y-1 -z-10" />
                <div className="absolute inset-0 bg-surface rounded-xl ring-1 ring-edge/10 rotate-1 translate-y-2 -z-20" />
              </>
            )}
            <InboxCard
              task={activeTask}
              selecting={selecting}
              selected={false}
              onToggle={() => {}}
              expanded={false}
              onExpand={() => {}}
              onRefresh={() => {}}
            />
            {/* Count badge for multi-drag */}
            {isMultiDrag && (
              <div className="absolute -top-2 -right-2 z-10 w-6 h-6 rounded-full bg-cyan-500 text-white text-xs font-bold flex items-center justify-center shadow-md">
                {selected.size}
              </div>
            )}
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
