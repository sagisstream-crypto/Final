// Адреса горячих кошельков бирж — на них завязан сигнал "net exchange flow"
// (вывод с биржи = накопление, занос на биржу = вероятная продажа).
//
// Специально оставлено пустым: подставлять адреса бирж по памяти рискованно —
// один неверный символ в hex-адресе даёт не "сигнал послабее", а тихо неверный
// сигнал, который ничем не отличается от правильного на вид. Возьмите адреса
// из проверяемого источника и впишите сюда:
//   - ярлыки прямо на etherscan.io/arbiscan.io (значок "Exchange" у адреса)
//   - публичные reference-списки, напр. github.com/dawsbot/crypto-exchange-addresses
//   - Nansen/Arkham public entity pages, если есть доступ
//
// Формат: chainId -> [{ address: "0x...", label: "Binance 14" }, ...]
// address всегда в нижнем регистре.
export const KNOWN_EXCHANGE_WALLETS = {
  1: [],
  42161: [],
  8453: [],
  56: [],
  137: [],
};

export function isKnownExchangeWallet(chainId, address) {
  const list = KNOWN_EXCHANGE_WALLETS[chainId] || [];
  const a = address?.toLowerCase();
  return list.some((w) => w.address === a);
}

export function exchangeWalletLabel(chainId, address) {
  const list = KNOWN_EXCHANGE_WALLETS[chainId] || [];
  const a = address?.toLowerCase();
  return list.find((w) => w.address === a)?.label || null;
}
