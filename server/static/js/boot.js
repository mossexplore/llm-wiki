    document.addEventListener('pointermove', moveGraphDrag);
    document.addEventListener('pointerup', endGraphDrag);
    document.addEventListener('pointercancel', endGraphDrag);
    document.addEventListener('mousemove', moveGraphDrag);
    document.addEventListener('mouseup', endGraphDrag);

    render();
    refreshMeta();
