export default function BotAvatar({
  size = 34,
  bg = "var(--primary-tint-3)",
}: {
  size?: number;
  bg?: string;
}) {
  const head = size * (12 / 34);
  const body = size * (23 / 34);
  const bodyH = size * (18 / 34);
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: 999,
        background: bg,
        overflow: "hidden",
        position: "relative",
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "50%",
          top: size * (6 / 34),
          transform: "translateX(-50%)",
          width: head,
          height: head,
          borderRadius: 999,
          background: "var(--primary)",
        }}
      />
      <span
        style={{
          position: "absolute",
          left: "50%",
          bottom: -(size * (9 / 34)),
          transform: "translateX(-50%)",
          width: body,
          height: bodyH,
          borderRadius: "999px 999px 0 0",
          background: "var(--primary)",
        }}
      />
    </div>
  );
}
