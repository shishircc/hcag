export type Source = { title: string; section: string; url?: string };

export type CardBullet = { text: string };

export type AnswerCard = {
  title: string;
  subtitle: string;
  bullets: CardBullet[];
};

export type ChatMessage = {
  id: string;
  role: "user" | "bot";
  text?: string;
  card?: AnswerCard;
  sources?: Source[];
  isEscalate?: boolean;
  hasFeedback?: boolean;
};

export type View = "min" | "docked" | "focus";
export type Device = "desktop" | "mobile";
export type Feedback = "up" | "down" | "downloaded" | null;
