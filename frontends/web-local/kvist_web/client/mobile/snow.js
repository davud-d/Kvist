// Pixel snow on a canvas over the page: square flakes of one or two pixel steps, a slow fall and a sway.
// It stops while the page is hidden, and with reduced motion it draws one still sprinkle.
const STEP = 3;
const FLAKES = 46;

export function startSnow(canvas) {
    const context = canvas.getContext('2d');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let flakes = [], width = 0, height = 0, raf = 0, last = 0;

    const spawn = fresh => ({
        x: Math.floor(Math.random() * width / STEP) * STEP,
        y: fresh ? -STEP * 2 : Math.floor(Math.random() * height / STEP) * STEP,
        size: Math.random() < 0.3 ? 2 : 1,
        speed: 14 + Math.random() * 22,
        phase: Math.random() * Math.PI * 2,
    });

    function resize() {
        const scale = devicePixelRatio || 1;
        width = canvas.clientWidth;
        height = canvas.clientHeight;
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        context.setTransform(scale, 0, 0, scale, 0, 0);
        flakes = Array.from({ length: FLAKES }, () => spawn(false));
    }

    function draw() {
        context.clearRect(0, 0, width, height);
        context.fillStyle = 'rgba(244, 248, 255, 0.85)';
        for (const flake of flakes) {
            const sway = Math.round(Math.sin(flake.phase) * 2) * STEP;
            const side = flake.size * STEP;
            context.fillRect(Math.round(flake.x + sway), Math.round(flake.y / STEP) * STEP, side, side);
        }
    }

    function frame(now) {
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;
        for (let i = 0; i < flakes.length; i++) {
            const flake = flakes[i];
            flake.y += flake.speed * dt;
            flake.phase += dt * 0.9;
            if (flake.y > height) flakes[i] = spawn(true);
        }
        draw();
        raf = requestAnimationFrame(frame);
    }

    function run() {
        cancelAnimationFrame(raf);
        if (reduced || document.hidden) return;
        last = performance.now();
        raf = requestAnimationFrame(frame);
    }

    canvas.hidden = false;
    resize();
    draw();
    addEventListener('resize', () => { resize(); draw(); });
    document.addEventListener('visibilitychange', run);
    run();
}
