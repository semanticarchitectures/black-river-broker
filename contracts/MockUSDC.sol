// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title MockUSDC
 * @notice Phase 1 stand-in for USDC on the local Hardhat chain.
 *         In Phase 2 this is replaced by real testnet USDC from
 *         Circle's faucet on the Tempo testnet.
 *
 *         6 decimals to match real USDC behaviour.
 */
contract MockUSDC is ERC20, Ownable {
    uint8 private constant _DECIMALS = 6;

    constructor(address initialOwner)
        ERC20("Mock USD Coin", "mUSDC")
        Ownable(initialOwner)
    {}

    function decimals() public pure override returns (uint8) {
        return _DECIMALS;
    }

    /**
     * @notice Mint tokens to any address.  Only callable by owner (deploy script).
     * @param to     Recipient address
     * @param amount Amount in micro-USDC (1 USDC = 1_000_000)
     */
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    /**
     * @notice Convenience: mint 1,000 USDC to caller.  For local dev use only.
     */
    function faucet() external {
        _mint(msg.sender, 1_000 * 10 ** _DECIMALS);
    }
}
