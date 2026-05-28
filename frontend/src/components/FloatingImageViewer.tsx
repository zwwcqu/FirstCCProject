import { useState, useRef, useEffect } from "react";

const HEADER_H = 32;
const FOOTER_H = 24;

interface FloatingImageViewerProps {
  src: string;
  title: string;
  visible: boolean;
  onClose: () => void;
  initialWidth?: number;
  initialHeight?: number;
  zIndex?: number;
  defaultTop?: number;
}

export default function FloatingImageViewer({
  src,
  title,
  visible,
  onClose,
  initialWidth = 360,
  initialHeight = 380,
  zIndex = 40,
  defaultTop = 80,
}: FloatingImageViewerProps) {
  const [pos, setPos] = useState<{ x: number; y: number }>({
    x: window.innerWidth - initialWidth - 16,
    y: defaultTop,
  });
  const [size, setSize] = useState({ w: initialWidth, h: initialHeight });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });

  const imgRef = useRef<HTMLDivElement>(null);
  const moveRef = useRef<{ mx: number; my: number; ox: number; oy: number } | null>(null);
  const resizeRef = useRef<{ mx: number; my: number; ow: number; oh: number } | null>(null);
  const panRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  // Document-level mousemove/mouseup for smooth drag, resize, and pan
  useEffect(() => {
    const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
    const onMouseMove = (e: MouseEvent) => {
      if (moveRef.current) {
        const x = moveRef.current.ox + (e.clientX - moveRef.current.mx);
        const y = moveRef.current.oy + (e.clientY - moveRef.current.my);
        setPos({
          x: clamp(x, -(sizeRef.current.w - 100), window.innerWidth - 100),
          y: clamp(y, 0, window.innerHeight - 40),
        });
      }
      if (resizeRef.current) {
        setSize({
          w: Math.max(240, resizeRef.current.ow + (e.clientX - resizeRef.current.mx)),
          h: Math.max(220, resizeRef.current.oh + (e.clientY - resizeRef.current.my)),
        });
      }
      if (panRef.current) {
        setPan({
          x: panRef.current.px + (e.clientX - panRef.current.x),
          y: panRef.current.py + (e.clientY - panRef.current.y),
        });
      }
    };
    const onMouseUp = () => {
      moveRef.current = null;
      resizeRef.current = null;
      panRef.current = null;
    };
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  // Wheel zoom on image area (native listener for Mac trackpad pinch)
  useEffect(() => {
    const el = imgRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.ctrlKey ? e.deltaY * 0.01 : e.deltaY * 0.002;
      setZoom((z) => Math.min(5, Math.max(0.5, z - delta)));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  // Keep panel in viewport on window resize
  useEffect(() => {
    const onResize = () => {
      setPos((p) => ({
        x: Math.min(p.x, window.innerWidth - 100),
        y: Math.min(p.y, window.innerHeight - 60),
      }));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    setPos({
      x: window.innerWidth - initialWidth - 16,
      y: defaultTop,
    });
  }, [defaultTop, initialWidth]);

  if (!visible) return null;

  const imgH = size.h - HEADER_H - FOOTER_H - 2; // border

  return (
    <div
      className="fixed bg-white rounded-lg shadow-2xl border border-gray-300 overflow-hidden"
      style={{ left: pos.x, top: pos.y, width: size.w, height: size.h, zIndex }}
    >
      {/* Title bar — drag handle */}
      <div
        className="flex items-center justify-between bg-gray-100 px-3 border-b cursor-grab active:cursor-grabbing select-none"
        style={{ height: HEADER_H }}
        onMouseDown={(e) => {
          moveRef.current = { mx: e.clientX, my: e.clientY, ox: pos.x, oy: pos.y };
        }}
      >
        <span className="text-xs font-medium text-gray-600 truncate">{title}</span>
        <button
          onClick={onClose}
          onMouseDown={(e) => e.stopPropagation()}
          className="text-gray-400 hover:text-gray-700 text-lg leading-none ml-2 flex-shrink-0"
        >&times;</button>
      </div>

      {/* Image area — zoom + pan */}
      <div
        ref={imgRef}
        className="overflow-hidden bg-gray-200 relative"
        style={{ height: imgH }}
        onMouseDown={(e) => {
          if (zoom <= 1) return;
          panRef.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
        }}
      >
        <img
          src={src}
          alt={title}
          className="w-full h-full object-contain"
          style={{
            transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
            transformOrigin: "center center",
          }}
          draggable={false}
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between bg-gray-50 px-3 text-xs text-gray-400"
        style={{ height: FOOTER_H }}>
        <span>滚轮/手势缩放</span>
        <span>{Math.round(zoom * 100)}%</span>
      </div>

      {/* Resize handle */}
      <div
        className="absolute bottom-0 right-0 w-5 h-5 cursor-se-resize rounded-br-lg"
        style={{ background: "linear-gradient(135deg, transparent 50%, #d1d5db 50%)" }}
        onMouseDown={(e) => {
          e.stopPropagation();
          e.preventDefault();
          resizeRef.current = { mx: e.clientX, my: e.clientY, ow: size.w, oh: size.h };
        }}
      />
    </div>
  );
}
