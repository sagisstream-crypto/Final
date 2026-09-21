// Etherscan V2 — один ключ, один базовый URL, сеть выбирается chainId.
// https://docs.etherscan.io/etherscan-v2
export const ETHERSCAN_V2_BASE = "https://api.etherscan.io/v2/api";

export const CHAINS = {
  1: { name: "ethereum", label: "Ethereum", dexscreenerId: "ethereum" },
  42161: { name: "arbitrum", label: "Arbitrum One", dexscreenerId: "arbitrum" },
  8453: { name: "base", label: "Base", dexscreenerId: "base" },
  56: { name: "bsc", label: "BNB Chain", dexscreenerId: "bsc" },
  137: { name: "polygon", label: "Polygon", dexscreenerId: "polygon" },
};

export function chainLabel(chainId) {
  return CHAINS[chainId]?.label || `chain ${chainId}`;
}
