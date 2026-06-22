import { useRef } from "react";

interface FileButtonProps {
  accept: string;
  onChange: (file: File) => void;
  label?: string;
  fileName?: string;       // 已选文件名（可选）
  className?: string;
}

export default function FileButton({ accept, onChange, label = "选择文件", fileName, className = "" }: FileButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className={`inline-flex items-center gap-2 ${className}`}>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onChange(f);
        }}
        className="hidden"
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 transition-colors"
      >
        {label}
      </button>
      {fileName && (
        <span className="text-xs text-gray-500 truncate max-w-[200px]" title={fileName}>
          {fileName}
        </span>
      )}
    </div>
  );
}
