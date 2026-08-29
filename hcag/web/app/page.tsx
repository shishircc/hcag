import HostPage from "@/components/host/HostPage";
import ChatWidget from "@/components/chat/ChatWidget";

export default function Page() {
  return (
    <>
      <HostPage />
      <ChatWidget botName="Work pass assistant" />
    </>
  );
}
