// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";

contract MigrateLiquidatedClusterTest is Test {
    // Define interfaces for SSV Core / Proxy contracts
    // (Import relevant SSV contract definitions here)

    function setUp() public {
        // Fork mainnet at a recent block where legacy clusters existed
        vm.createSelectFork("mainnet", 26_305_727); // adjust block number as needed
    }

    function test_MigrateLiquidatedClusterSSVWipe() public {
        // 1. Target a known legacy cluster address / owner that is liquidated (active = false, balance > 0)
        address clusterOwner = address(0xe2758939507b35A22C30039A7da04B54cdc4c9A0); // fill from event logs / Dune query
        
        // 2. Impersonate the owner and check initial balance
        vm.startPrank(clusterOwner);
        
        // 3. Call migrateClusterToETH and capture the ssvRefunded value or event
        // uint256 balanceBefore = ssvToken.balanceOf(clusterOwner);
        // ssvProxy.migrateClusterToETH(...);
        // uint256 balanceAfter = ssvToken.balanceOf(clusterOwner);
        
        // 4. Assert whether the SSV balance was improperly wiped
        // assertEq(balanceAfter - balanceBefore, expectedRefund);

        // Interfaces for SSVNetwork + SSVToken, pointed at:
        // SSVNetwork proxy: 0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1  (check digit count against the Immunefi scope page)
        // SSV token address

        uint256 before = ssvToken.balanceOf(clusterOwner);
        ssvNetwork.migrateClusterToETH(operatorIds, cluster); // cluster struct from your subgraph row
        uint256 afterBal = ssvToken.balanceOf(clusterOwner);
        console2.log("lost", before - afterBal);
        assertGt(before, 0); // sanity: cluster actually had SSV before migrating

        
        vm.stopPrank();
    }
}
